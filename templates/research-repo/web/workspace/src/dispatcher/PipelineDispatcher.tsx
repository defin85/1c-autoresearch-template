// Управляемая React Flow-мнемосхема диспетчера конвейера.
//
// Схема читает фиксированные узлы/рёбра и серверную проекцию; перетаскивание,
// соединение, удаление и сохранение компоновки отключены. Разрешены
// панорамирование, масштабирование, ``fitView`` и выбор узла для панели
// подробностей. ``prefers-reduced-motion`` отключает анимацию без потери данных.

import { Component, useCallback, useEffect, useRef, useState, type ErrorInfo, type ReactNode } from 'react';
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControl, FormControlLabel, InputLabel, LinearProgress, MenuItem, Paper, Select, Stack, TextField, Typography } from '@mui/material';
import { Check, CheckCircle, DataObject, Hub, TaskAlt, WarningAmber } from '@mui/icons-material';
import { ReactFlowProvider, type Viewport } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api, mutationHeaders } from '../api';
import { freshnessLabel, type CircuitId, type CircuitLease, type DispatcherProjection } from './projection';
import { DispatcherNew } from './DispatcherNew';
import { useDispatcherStream } from './useDispatcherStream';

const CIRCUIT_LABEL: Record<CircuitId, string> = {
  'prepare-diffs': 'Подготовка различий',
  'analyze-dif': 'Анализ DIF',
  'form-mrq': 'Формирование MRQ',
  'classify-mrq': 'Формирование пакетов',
  'decide-target': 'Исследование цели',
};

const short = (value: string, size = 22) => value.length > size ? `${value.slice(0, size)}…` : value;
function leasePresentation(lease: Pick<CircuitLease, 'state' | 'renewed_at'> | undefined, runningLabel: string) {
  if (!lease) return { state: 'Свободен', active: false };
  if (lease.state === 'running' && Date.now() - Date.parse(lease.renewed_at) >= 30_000) return { state: 'Аренда истекла', active: false };
  if (lease.state === 'running') return { state: runningLabel, active: true };
  return { state: ({ failed: 'Ошибка', resumable: 'Остановлен', blocked: 'Ожидает одобрения', stale: 'Устарел' } as Record<string, string>)[lease.state] ?? lease.state, active: false };
}

interface DispatcherActionPayload {
  actor?: string;
  work_unit_id?: string;
  instruction_supplement?: string;
  timeout_seconds?: number;
  proposal_key?: string;
  payload?: Record<string, unknown>;
  expected_fingerprint?: string;
  policy_source?: 'reuse-snapshot' | 'current-policy';
  predecessor_run_id?: string;
  expected_workflow_fingerprint?: string;
  expected_input_fingerprints?: {
    source_generation_id: string;
    diff_generation_id: string;
    canonical_generation_id: string;
    work_unit_id: string;
  };
}

interface DispatcherOutcome {
  status: string;
  run_id: string;
  thread_id: string;
  revision: number;
  summary: Record<string, unknown>;
  blocker?: { code: string; message: string; action: string };
}

interface DispatcherActionResponse {
  job_id: string;
  action: string;
  outcome: DispatcherOutcome;
}

interface RecomputePlan {
  boundary: string;
  plan_fingerprint: string;
  workflow_fingerprint?: string;
  steps: Array<{ operation?: string; step_id?: string; kind?: string; conditional?: boolean }>;
  required_confirmations?: string[];
  possible_result?: string;
  possible_results?: string[];
  stop_before?: string;
  manual_stop?: string | null;
  generations?: Record<string, string>;
  active_pointers?: Record<string, { generation_id?: string; canonical_generation_id?: string } | null>;
}

interface RecomputeRun {
  run_id: string;
  status: string;
  plan_fingerprint: string;
  result?: string | Record<string, unknown>;
  next_work_unit_id?: string;
}

async function postDispatcherAction(projectId: string, jobId: 'discover-mrq' | 'classify-mrq' | 'decide-mrq', action: string, body: DispatcherActionPayload): Promise<DispatcherOutcome> {
  const response = await api<DispatcherActionResponse>(`/projects/${projectId}/dispatcher/${jobId}/${action}`, {
    method: 'POST',
    headers: mutationHeaders(),
    body: JSON.stringify(body),
  });
  return response.outcome;
}

interface ContextActions { onOpenSources?: () => void; onOpenIndexes?: () => void; onOpenSettings?: (stepId?: string) => void }

type LeaseAction = 'start' | 'stop' | 'resume' | 'retry' | 'cancel' | 'approve';

export function leaseActionMatrix(
  lease: (Pick<CircuitLease, 'state' | 'renewed_at'> & Partial<CircuitLease>) | undefined,
  circuitReady: boolean,
  recomputeActive: boolean,
  approvalAvailable: boolean,
  now = Date.now(),
): Record<LeaseAction, boolean> {
  const expired = lease?.state === 'running' && now - Date.parse(lease.renewed_at) >= 30_000;
  const state = expired ? 'expired' : lease?.state ?? 'absent';
  return {
    start: state === 'absent' && circuitReady && !recomputeActive,
    stop: state === 'running',
    resume: state === 'resumable' && !recomputeActive,
    retry: ['resumable', 'failed', 'stale', 'expired'].includes(state) && !recomputeActive,
    cancel: state !== 'absent' && state !== 'complete',
    approve: state === 'blocked' && approvalAvailable,
  };
}

export function DispatcherPanel({ projectId, projection, fingerprint, selectedCircuit, onClose, onOpenSources, onOpenIndexes, onOpenSettings, readOnly = false }: { projectId: string; projection: DispatcherProjection; fingerprint: string; selectedCircuit: CircuitId | null; onClose: () => void; readOnly?: boolean } & ContextActions) {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<DispatcherOutcome | null>(null);
  const [plan, setPlan] = useState<RecomputePlan | null>(null);
  const [confirmations, setConfirmations] = useState<string[]>([]);
  const [recomputeRun, setRecomputeRun] = useState<RecomputeRun | null>(null);
  const [error, setError] = useState('');
  const [retryOpen, setRetryOpen] = useState(false);
  const [policySource, setPolicySource] = useState<'reuse-snapshot' | 'current-policy'>('reuse-snapshot');
  const [predecessorRunId, setPredecessorRunId] = useState('');
  const job = selectedCircuit === 'analyze-dif' || selectedCircuit === 'form-mrq' ? 'discover-mrq' : selectedCircuit === 'classify-mrq' ? 'classify-mrq' : selectedCircuit === 'decide-target' ? 'decide-mrq' : null;
  const lease = job ? projection.jobs[job] : undefined;
  const recomputeLease = projection.jobs['stage-recompute'];
  const recomputeActive = recomputeLease?.state === 'running';
  useEffect(() => {
    const latest = projection.stage_recompute_run;
    if (latest && (!recomputeRun || latest.run_id === recomputeRun.run_id)) {
      const next = latest.result?.next as { work_unit?: { id?: string }; id?: string } | undefined;
      setRecomputeRun({
        run_id: latest.run_id,
        status: latest.status,
        plan_fingerprint: latest.plan?.plan_fingerprint || recomputeRun?.plan_fingerprint || '',
        result: latest.result || undefined,
        next_work_unit_id: next?.work_unit?.id || next?.id,
      });
    }
  }, [projection.stage_recompute_run, recomputeRun?.run_id]);
  const pendingApproval = job ? projection.items.proposals.find((proposal) => proposal.job_id === job && proposal.kind === 'approval') : undefined;
  const retryCandidates = job ? (projection.retry_candidates || []).filter((item) => item.job_id === job) : [];
  const circuitReady = projection.circuits.find((item) => item.id === selectedCircuit)?.state === 'ready';
  const actions = leaseActionMatrix(lease, circuitReady, recomputeActive, Boolean(pendingApproval));
  actions.retry = actions.retry || (!recomputeActive && retryCandidates.length > 0);

  const run = useCallback(async (action: 'start' | 'stop' | 'resume' | 'cancel', extra: DispatcherActionPayload = {}) => {
    if (!job) return;
    setBusy(true);
    setError('');
    try {
      const body = action === 'stop' ? extra : { actor: 'local-user', ...extra };
      const result = await postDispatcherAction(projectId, job, action, body);
      setOutcome(result);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'dispatcher action failed');
    } finally {
      setBusy(false);
    }
  }, [job, projectId, fingerprint]);

  const retry = useCallback(async () => {
    if (!job || !predecessorRunId.trim()) return;
    const candidate = retryCandidates.find((item) => item.run_id === predecessorRunId);
    const bindings = (candidate?.input_fingerprints || lease?.summary?.bindings || {}) as Record<string, string>;
    setBusy(true);
    setError('');
    try {
      setOutcome(await postDispatcherAction(projectId, job, 'retry', {
        policy_source: policySource,
        predecessor_run_id: predecessorRunId.trim(),
        expected_workflow_fingerprint: fingerprint,
        expected_input_fingerprints: {
          source_generation_id: bindings.source_generation_id || '',
          diff_generation_id: bindings.diff_generation_id || '',
          canonical_generation_id: bindings.canonical_generation_id || '',
          work_unit_id: bindings.work_unit_id || lease?.work_unit_id || '',
        },
      }));
      setRetryOpen(false);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Явный повтор отклонён как несовместимый');
    } finally {
      setBusy(false);
    }
  }, [fingerprint, job, lease, policySource, predecessorRunId, projectId, retryCandidates]);

  const approve = useCallback(async () => {
    if (!job || !pendingApproval) return;
    setBusy(true);
    setError('');
    try {
      const action = pendingApproval.approval_stage === 'noise'
        ? 'approve-noise'
        : job === 'discover-mrq' ? 'approve-batch' : 'approve-decision';
      const result = await postDispatcherAction(projectId, job, action, {
        actor: 'local-user',
        proposal_key: pendingApproval.id,
        expected_fingerprint: fingerprint,
      });
      setOutcome(result);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'approval failed');
    } finally {
      setBusy(false);
    }
  }, [job, pendingApproval, projectId, fingerprint]);

  const previewRecompute = useCallback(async () => {
    setBusy(true);
    setError('');
    setPlan(null);
    setConfirmations([]);
    try {
      const result = await api<RecomputePlan>(`/projects/${projectId}/stage-recompute/preview`, {
        method: 'POST',
        headers: mutationHeaders(),
        body: JSON.stringify({ boundary: 'diffs', expected_workflow_fingerprint: fingerprint }),
      });
      setPlan(result);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Не удалось построить план пересчёта');
    } finally {
      setBusy(false);
    }
  }, [fingerprint, projectId]);

  const startRecompute = useCallback(async () => {
    if (!plan || confirmations.length !== (plan.required_confirmations ?? []).length) return;
    setBusy(true);
    setError('');
    try {
      const result = await api<RecomputeRun>(`/projects/${projectId}/stage-recompute/runs`, {
        method: 'POST',
        headers: mutationHeaders(),
        body: JSON.stringify({
          boundary: 'diffs',
          workflow_fingerprint: fingerprint,
          plan_fingerprint: plan.plan_fingerprint,
          confirmations: [...confirmations].sort(),
        }),
      });
      setRecomputeRun(result);
      setPlan(null);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Не удалось запустить пересчёт');
    } finally {
      setBusy(false);
    }
  }, [confirmations, fingerprint, plan, projectId]);

  const cancelRecompute = useCallback(async () => {
    const runId = String(recomputeLease?.summary?.run_id || recomputeRun?.run_id || '');
    if (!runId) return;
    setBusy(true);
    setError('');
    try {
      setRecomputeRun(await api<RecomputeRun>(`/projects/${projectId}/stage-recompute/runs/${encodeURIComponent(runId)}/cancel`, {
        method: 'POST',
        headers: mutationHeaders(),
        body: JSON.stringify({}),
      }));
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'Не удалось отменить пересчёт');
    } finally {
      setBusy(false);
    }
  }, [projectId, recomputeLease, recomputeRun]);

  if (!selectedCircuit) return null;
  return (
    <Paper elevation={8} className="nodrag nowheel" sx={{ position: 'absolute', zIndex: 5, right: 16, bottom: 16, width: 500, maxWidth: 'calc(100% - 32px)', maxHeight: 'calc(100% - 32px)', overflowY: 'auto', p: 2 }}>
    <Stack spacing={1.4}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" alignItems="center"><Typography variant="subtitle1" fontWeight={700} flex={1}>{CIRCUIT_LABEL[selectedCircuit]}</Typography><Button size="small" onClick={onClose}>Закрыть</Button></Stack>
      {selectedCircuit === 'prepare-diffs' ? <Stack direction="row" spacing={1} flexWrap="wrap">{onOpenSources && <Button variant="contained" onClick={onOpenSources}>Настроить источники</Button>}{onOpenIndexes && <Button variant="outlined" onClick={onOpenIndexes}>Проверить индексы</Button>}</Stack> : <>
      <Paper component="section" variant="outlined" aria-labelledby="current-job-title" sx={{ p: 1.4 }}>
        <Typography id="current-job-title" variant="subtitle2" fontWeight={700}>Текущее задание</Typography>
        <Typography variant="caption" display="block" mb={1}>Аренда: {lease ? `${lease.owner} (${leasePresentation(lease, '').state})` : 'не захвачена'} · ревизия {projection.revision}</Typography>
        {recomputeActive && <Alert severity="info" sx={{ mb: 1 }}>Идёт пересчёт этапа. Запуск и продолжение обычного задания временно недоступны.</Alert>}
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
          <Button variant="contained" disabled={readOnly || busy || !actions.start} onClick={() => void run('start')}>Запустить</Button>
          <Button variant="outlined" disabled={readOnly || busy || !actions.stop} onClick={() => void run('stop')}>Мягкая остановка</Button>
          <Button variant="outlined" disabled={readOnly || busy || !actions.resume} onClick={() => void run('resume')}>Продолжить</Button>
          <Button color="warning" disabled={readOnly || busy || !actions.retry} onClick={() => { setPredecessorRunId(String(lease?.summary?.run_id || retryCandidates[0]?.run_id || '')); setRetryOpen(true); }}>Явный повтор</Button>
          <Button color="error" disabled={readOnly || busy || !actions.cancel} onClick={() => void run('cancel')}>Отменить задание</Button>
          {pendingApproval && <Button color="success" variant="contained" disabled={readOnly || busy || !actions.approve} onClick={() => void approve()}>{pendingApproval.approval_stage === 'noise' ? `Одобрить шум (${pendingApproval.noise_count})` : 'Одобрить предложение'}</Button>}
          {onOpenSettings && <Button onClick={() => onOpenSettings(job || undefined)}>Профили и параметры</Button>}
        </Stack>
        {lease?.state === 'resumable' && (
          <Alert severity="info" sx={{ mt: 1 }}>
            Продолжение использует неизменяемый исходный снимок{" "}
            {String(lease.summary?.execution_snapshot_fingerprint || 'без доступного отпечатка')}
            ; текущая политика не перечитывается.
          </Alert>
        )}
      </Paper>
      <Paper component="section" variant="outlined" aria-labelledby="recompute-title" sx={{ p: 1.4 }}>
        <Typography id="recompute-title" variant="subtitle2" fontWeight={700}>Пересчёт этапа</Typography>
        {selectedCircuit === 'analyze-dif' ? <Stack spacing={1} mt={0.7}>
          <Typography variant="body2">Граница: различия. Сначала будет построен план без изменения репозитория.</Typography>
          {recomputeActive ? <>
            <Alert severity="info">Выполняется шаг: {String(recomputeLease.summary?.current_step || recomputeLease.summary?.step_id || 'подготовка')}</Alert>
            <Button color="error" variant="outlined" disabled={readOnly || busy} onClick={() => void cancelRecompute()}>Отменить пересчёт</Button>
          </> : <Button variant="outlined" disabled={readOnly || busy || Boolean(lease)} onClick={() => void previewRecompute()}>Пересчитать с этапа</Button>}
          {lease && !recomputeActive && <Typography variant="caption">Сначала завершите, повторите или отмените текущее задание.</Typography>}
          {plan && <Paper variant="outlined" sx={{ p: 1.2 }} aria-label="Предварительный просмотр пересчёта">
            <Typography variant="body2" fontWeight={700}>План пересчёта</Typography>
            <Typography variant="caption" display="block">Отпечаток: {plan.plan_fingerprint}</Typography>
            {plan.generations && <Typography variant="caption" display="block">Поколения: {Object.entries(plan.generations).map(([key, value]) => `${key}=${value}`).join(', ')}</Typography>}
            {plan.active_pointers && <Typography variant="caption" display="block">Поколения: {Object.entries(plan.active_pointers).map(([key, value]) => `${key}=${value?.generation_id || value?.canonical_generation_id || 'нет'}`).join(', ')}</Typography>}
            <Stack component="ol" spacing={0.4} sx={{ pl: 2.5, my: 1 }}>
              {plan.steps.map((step, index) => <Typography component="li" variant="body2" key={step.step_id || `${step.operation}-${index}`}>{step.operation || step.kind || step.step_id}{step.conditional ? ' (условно)' : ''}</Typography>)}
            </Stack>
            {(plan.possible_result || plan.possible_results) && <Typography variant="body2">Возможный результат: {plan.possible_result || plan.possible_results?.join(', ')}</Typography>}
            {(plan.stop_before || plan.manual_stop) && <Typography variant="body2">Ближайшая ручная остановка: {plan.stop_before || plan.manual_stop}</Typography>}
            {(plan.required_confirmations ?? []).map((confirmation) => <FormControlLabel key={confirmation} control={<Checkbox checked={confirmations.includes(confirmation)} onChange={(_, checked) => setConfirmations((current) => checked ? [...current, confirmation] : current.filter((item) => item !== confirmation))} />} label={`Подтверждаю: ${confirmation}`} />)}
            <Button variant="contained" disabled={readOnly || busy || confirmations.length !== (plan.required_confirmations ?? []).length} onClick={() => void startRecompute()}>Запустить пересчёт</Button>
          </Paper>}
          {recomputeRun && <Alert severity={recomputeRun.status === 'failed' ? 'warning' : 'success'}>Пересчёт: {typeof recomputeRun.result === 'object' ? String(recomputeRun.result.status || recomputeRun.status) : recomputeRun.result || recomputeRun.status}{recomputeRun.next_work_unit_id ? `; следующая работа: ${recomputeRun.next_work_unit_id}` : ''}</Alert>}
        </Stack> : <Alert severity="info" sx={{ mt: 0.7 }}>Для этапов MRQ массовый сброс не поддерживается. Используйте явный повтор, пересмотр или реструктуризацию текущего задания.</Alert>}
      </Paper>
      </>}
      {outcome && (
        <Alert severity={outcome.status === 'failed' || outcome.status === 'blocked' || outcome.status === 'stale' ? 'warning' : 'success'}>
          <Typography>Статус: {outcome.status}; ревизия: {outcome.revision}</Typography>
          {outcome.blocker && <Typography variant="caption">{outcome.blocker.code}: {outcome.blocker.message}</Typography>}
        </Alert>
      )}
      {job && <Typography variant="caption">Автоматический повтор отключён; повтор запускается только явно.</Typography>}
      <Dialog open={retryOpen} onClose={() => setRetryOpen(false)} aria-labelledby="retry-title">
        <DialogTitle id="retry-title">Явный повтор задания</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1, minWidth: 360 }}>
            <FormControl>
              <InputLabel id="retry-policy-label">Источник политики</InputLabel>
              <Select labelId="retry-policy-label" label="Источник политики" value={policySource} onChange={(event) => setPolicySource(event.target.value as typeof policySource)}>
                <MenuItem value="reuse-snapshot">Исходный снимок</MenuItem>
                <MenuItem value="current-policy">Текущая политика</MenuItem>
              </Select>
            </FormControl>
            <TextField select required label="Запуск-предшественник" value={predecessorRunId} onChange={(event) => setPredecessorRunId(event.target.value)}>
              {[
                ...(lease?.summary?.run_id ? [{ run_id: String(lease.summary.run_id), status: lease.state }] : []),
                ...retryCandidates,
              ].filter((item, index, items) => items.findIndex((other) => other.run_id === item.run_id) === index).map((item) => (
                <MenuItem key={item.run_id} value={item.run_id}>{item.run_id} · {item.status}</MenuItem>
              ))}
            </TextField>
            <Alert severity="info">
              Будут проверены отпечатки workflow и предметных входов. Повреждённый, отсутствующий или несовместимый предшественник будет отклонён.
            </Alert>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRetryOpen(false)}>Отмена</Button>
          <Button variant="contained" disabled={readOnly || busy || !predecessorRunId.trim()} onClick={() => void retry()}>Запустить повтор</Button>
        </DialogActions>
      </Dialog>
    </Stack>
    </Paper>
  );
}

export class DispatcherCanvasErrorBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: '' };
  static getDerivedStateFromError(error: Error) {
    return { error: error.message || 'Ошибка нового холста' };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {}
  render() {
    return this.state.error
      ? <Alert severity="error">Холст диспетчера недоступен: {this.state.error}</Alert>
      : this.props.children;
  }
}

type RecentEvent = { sequence: number; timestamp: string; type: string; payload: { kind?: string; work_unit_id?: string } };

function StageRail({ projection }: { projection: DispatcherProjection }) {
  const states = Object.fromEntries(projection.circuits.map((circuit) => [circuit.id, circuit.state]));
  const stages = [
    ['Подготовка', '#1976d2'],
    ['Анализ DIF', '#1976d2'],
    ['Формирование MRQ', '#6d3be7'],
    ['Формирование пакетов', '#7b1fa2'],
    ['Исследование цели', '#0097a7'],
  ] as const;
  const runningJob = Object.entries(projection.jobs).find(([, lease]) => lease.state === 'running')?.[0];
  const readyCircuit = projection.circuits.find((circuit) => circuit.state === 'ready' || circuit.state === 'blocked')?.id;
  const active = runningJob === 'decide-mrq' || readyCircuit === 'decide-target' ? 4
    : runningJob === 'classify-mrq' || readyCircuit === 'classify-mrq' ? 3
    : runningJob === 'discover-mrq' || readyCircuit === 'form-mrq' ? 2
      : readyCircuit === 'analyze-dif' ? 1
      : projection.items.decisions.length ? 4
        : projection.items.batches.length ? 3
          : projection.items.mrqs.length ? 2
            : projection.items.meaning_diffs.length || projection.items.proposals.length ? 2
              : states['prepare-diffs'] === 'complete' ? 1 : 0;
  return <Stack direction="row" alignItems="flex-start" justifyContent="center" sx={{ flex: 1, minWidth: 760 }}>{stages.map(([label, stageColor], index) => {
    const complete = index < active;
    const current = index === active;
    const blocked = current && Object.values(states).includes('blocked');
    const color = blocked ? '#d32f2f' : current ? stageColor : complete ? '#2e7d32' : '#9e9e9e';
    return <Stack key={label} direction="row" alignItems="flex-start" sx={{ flex: index < stages.length - 1 ? 1 : 'none' }}><Stack alignItems="center" spacing={0.15} data-stage-state={complete ? 'complete' : current ? 'active' : 'future'}><Box sx={{ width: 32, height: 32, borderRadius: '50%', display: 'grid', placeItems: 'center', border: '2px solid', borderColor: color, bgcolor: current ? color : '#fff', color: current ? '#fff' : color, fontWeight: 750 }}>{complete ? <Check fontSize="small" /> : index + 1}</Box><Typography variant="body2" fontSize={13} fontWeight={current ? 750 : 500} color={color} whiteSpace="nowrap">{label}</Typography><Typography variant="caption" fontSize={11} color={color}>{complete ? 'Завершён' : current ? blocked ? 'Ожидает' : 'Текущий' : 'Впереди'}</Typography></Stack>{index < stages.length - 1 && <Box sx={{ flex: 1, height: 2, bgcolor: complete ? '#2e7d32' : '#cbd5e1', mt: 1.9, mx: 1 }} />}</Stack>;
  })}</Stack>;
}

function DispatcherFooter({ projection, events }: { projection: DispatcherProjection; events: RecentEvent[] }) {
  const stale = freshnessLabel(projection) === 'данные несвежие';
  const active = Object.values(projection.jobs).some((lease) => lease.state === 'running');
  const summary = stale ? 'Данные диспетчера устарели' : active ? 'Исследование выполняется' : 'Операционное состояние подтверждено';
  return <Box display="grid" gridTemplateColumns="1.15fr 1.35fr 1fr" gap={1.5}><Paper variant="outlined" sx={{ p: 1.1, minHeight: 84 }}><Stack direction="row" justifyContent="space-between"><Typography variant="subtitle2" fontWeight={700}>Последние события</Typography><Typography variant="caption" color="primary">Журнал</Typography></Stack>{events.length ? events.slice(0, 3).map((event) => <Stack key={event.sequence} direction="row" spacing={1} mt={0.35}><Typography variant="caption" color="text.secondary">{new Date(event.timestamp).toLocaleTimeString('ru-RU')}</Typography><Typography variant="caption" title={event.payload.kind || event.type}>{short(event.payload.kind || event.type, 34)}</Typography></Stack>) : <Typography variant="body2" fontSize={13} color="text.secondary" display="block" mt={0.7}>Переходов диспетчера пока нет</Typography>}</Paper><Paper variant="outlined" sx={{ p: 1.1, minHeight: 84 }}><Typography variant="subtitle2" fontWeight={700} mb={0.6}>Легенда</Typography><Stack direction="row" gap={1.5} flexWrap="wrap">{[['#1976d2', 'DIF и анализ'], ['#6d3be7', 'Группировка и MRQ'], ['#0097a7', 'Исследование цели'], ['#2e7d32', 'Типовой функционал'], ['#ed6c02', 'Функциональный разрыв'], ['#d32f2f', 'Ошибка']].map(([color, label]) => <Stack direction="row" spacing={0.7} alignItems="center" key={label}><Box sx={{ width: 24, height: 4, borderRadius: 2, bgcolor: color }} /><Typography variant="caption">{label}</Typography></Stack>)}</Stack></Paper><Paper variant="outlined" sx={{ p: 1.1, minHeight: 84 }}><Stack direction="row" spacing={1.2} alignItems="center" height="100%"><CheckCircle color={stale ? 'warning' : 'success'} sx={{ fontSize: 36 }} /><Box><Typography variant="subtitle1" fontWeight={700}>{summary}</Typography><Typography variant="body2" color="text.secondary">{freshnessLabel(projection)}</Typography></Box></Stack></Paper></Box>;
}

export interface PipelineDispatcherProps {
  projectId: string;
  initialProjection?: DispatcherProjection;
  initialFingerprint?: string;
  onOpenSources?: () => void;
  onOpenIndexes?: () => void;
  onOpenSettings?: (stepId?: string) => void;
  onOpenJournal?: () => void;
  onOpenRegistries?: () => void;
}

export function PipelineDispatcher({ projectId, initialProjection, initialFingerprint, onOpenSources, onOpenIndexes, onOpenSettings, onOpenJournal, onOpenRegistries }: PipelineDispatcherProps) {
  const { projection, fingerprint, resyncing, error, refresh } = useDispatcherStream({ projectId, initialProjection, initialFingerprint });
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>([]);
  const [selectedCircuit, setSelectedCircuit] = useState<CircuitId | null>(null);
  const [initiatorKey, setInitiatorKey] = useState<string | null>(null);
  const [viewport, setViewport] = useState<Viewport>({ x: 4, y: 10, zoom: 0.9 });
  const shellRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    void api<{ events: RecentEvent[]; snapshot?: { events: RecentEvent[] }[] }>(`/projects/${projectId}/events?cursor=0&limit=50`).then((value) => {
      const events = value.events.length ? value.events : (value.snapshot ?? []).flatMap((item) => item.events ?? []);
      setRecentEvents(events.filter((event) => event.payload.kind?.startsWith('dispatcher.')).slice(-4).reverse());
    }).catch(() => setRecentEvents([]));
  }, [projectId, projection.revision]);
  if (resyncing && !initialProjection && !error) {
    return <Stack alignItems="center" spacing={2}><CircularProgress /><Typography>Пересинхронизация операционного состояния&hellip;</Typography></Stack>;
  }
  const activate = (circuitId: CircuitId, key: string) => {
    setInitiatorKey(key);
    setSelectedCircuit(circuitId);
  };
  const closePanel = () => {
    const circuit = selectedCircuit;
    setSelectedCircuit(null);
    setTimeout(() => {
      const separator = initiatorKey?.indexOf(':') ?? -1;
      const source = separator < 0 ? '' : initiatorKey!.slice(0, separator);
      const nodeId = separator < 0 ? '' : initiatorKey!.slice(separator + 1);
      const graphNode = source === 'new'
        ? [...(shellRef.current?.querySelectorAll<HTMLElement>('.react-flow__node[data-id]') ?? [])]
          .find((element) => element.dataset.id === nodeId)
        : undefined;
      const invocation = source === 'new-invocation' || source === 'new-slot'
        ? [...(shellRef.current?.querySelectorAll<HTMLElement>('[data-dispatcher-initiator]') ?? [])]
          .find((element) => element.dataset.dispatcherInitiator === initiatorKey)
        : undefined;
      const exact = graphNode ?? invocation;
      (exact?.isConnected ? exact : shellRef.current?.querySelector<HTMLElement>(`[data-dispatcher-nav="${circuit}"]`))?.focus();
    }, 0);
  };
  return (
    <Stack spacing={1} ref={shellRef}>
      {error && <Alert severity="warning" action={resyncing ? <Button color="inherit" onClick={() => void refresh()}>Повторить снимок</Button> : undefined}>{error}</Alert>}
      {resyncing && !error && <LinearProgress aria-label="Пересинхронизация операционного состояния" />}
      <Stack direction="row" alignItems="center" spacing={2}><StageRail projection={projection} /><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={freshnessLabel(projection) === 'данные несвежие' ? 'warning' : 'success'} label={freshnessLabel(projection)} /><Typography variant="caption" color="text.secondary">Ревизия {projection.revision}</Typography>{onOpenJournal && <Button size="small" onClick={onOpenJournal}>Журнал</Button>}{onOpenRegistries && <Button size="small" onClick={onOpenRegistries}>Реестры</Button>}</Stack></Stack>
      <Stack direction="row" spacing={1} component="nav" aria-label="Этапы диспетчера" useFlexGap flexWrap="wrap">
        {(Object.keys(CIRCUIT_LABEL) as CircuitId[]).map((circuit) => (
          <Button
            key={circuit}
            size="small"
            data-dispatcher-nav={circuit}
            variant={selectedCircuit === circuit ? 'contained' : 'outlined'}
            onClick={() => activate(circuit, `nav:${circuit}`)}
          >
            {CIRCUIT_LABEL[circuit]}
          </Button>
        ))}
      </Stack>
      <ReactFlowProvider>
        <Box sx={{ position: 'relative' }} onKeyDown={(event) => {
          if (event.key === 'Escape' && selectedCircuit) closePanel();
        }}>
          <DispatcherCanvasErrorBoundary>
            <DispatcherNew
              projection={projection}
              viewport={viewport}
              onMoveEnd={setViewport}
              onActivate={activate}
            />
          </DispatcherCanvasErrorBoundary>
          <DispatcherPanel
            projectId={projectId}
            projection={projection}
            fingerprint={fingerprint}
            selectedCircuit={selectedCircuit}
            onClose={closePanel}
            onOpenSources={onOpenSources}
            onOpenIndexes={onOpenIndexes}
            onOpenSettings={onOpenSettings}
            readOnly={resyncing}
          />
        </Box>
      </ReactFlowProvider>
      <DispatcherFooter projection={projection} events={recentEvents} />
    </Stack>
  );
}
