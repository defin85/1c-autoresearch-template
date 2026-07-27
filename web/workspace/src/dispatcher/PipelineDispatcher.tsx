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
import { freshnessLabel, type CircuitId, type CircuitLease, type DispatcherProjection, type DispatcherSelection } from './projection';
import { DispatcherNew } from './DispatcherNew';
import { useDispatcherStream } from './useDispatcherStream';

const CIRCUIT_LABEL: Record<CircuitId, string> = {
  'prepare-diffs': 'Подготовка различий',
  'analyze-dif': 'Анализ DIF',
  'form-mrq': 'Формирование и консолидация MRQ',
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

async function postDispatcherAction(projectId: string, jobId: 'analyze-dif' | 'consolidate-mrq' | 'classify-mrq' | 'decide-mrq', action: string, body: DispatcherActionPayload): Promise<DispatcherOutcome> {
  const response = await api<DispatcherActionResponse>(`/projects/${projectId}/dispatcher/${jobId}/${action}`, {
    method: 'POST',
    headers: mutationHeaders(),
    body: JSON.stringify(body),
  });
  return response.outcome;
}

export interface JournalTarget { runId?: string; invocationId?: string }
export interface RegistryTarget { registry: 'diff-inventory' | 'mrq'; itemId: string }
interface ContextActions {
  onOpenSources?: () => void;
  onOpenIndexes?: () => void;
  onOpenSettings?: (stepId?: string) => void;
  onOpenJournal?: (target?: JournalTarget) => void;
  onOpenRegistry?: (target: RegistryTarget) => void;
}

interface InspectionPage<T> { items: T[]; next_cursor?: string | null; truncated?: boolean; available?: boolean }
interface DispatcherInspection {
  observed_at?: string;
  slot?: Record<string, unknown>;
  invocation?: Record<string, unknown>;
  history?: InspectionPage<Record<string, unknown>>;
  events?: InspectionPage<Record<string, unknown>>;
  result?: Record<string, unknown>;
  availability?: Record<string, unknown>;
}

const selectionKey = (selection: DispatcherSelection) => JSON.stringify(selection);
const IDLE_REASON: Record<string, string> = {
  work_not_requested: 'Работа ещё не запрошена',
  waiting_for_prerequisite: 'Ожидается завершение предыдущего этапа',
  waiting_for_dispatch: 'Ожидается назначение работы',
  queue_empty: 'Очередь пуста',
  phase_complete: 'Работа этапа завершена',
  environment_unavailable: 'Окружение недоступно',
};
const COLLECTIONS = {
  'dif-queue': { title: 'Очередь DIF', itemKind: 'dif', items: (projection: DispatcherProjection) => projection.items.dif_queue },
  'meaning-diffs': { title: 'Смысловые DIF', itemKind: 'dif', items: (projection: DispatcherProjection) => projection.items.meaning_diffs },
  'noise-diffs': { title: 'Технический шум', itemKind: 'dif', items: (projection: DispatcherProjection) => projection.items.noise_diffs },
  proposals: { title: 'Предложения групп', itemKind: 'proposal', items: (projection: DispatcherProjection) => projection.items.proposals },
  'mrq-outcomes': { title: 'Исходы консолидации MRQ', itemKind: 'outcome', items: (projection: DispatcherProjection) => projection.items.mrq_outcomes },
  'mrq-queue': { title: 'MRQ', itemKind: 'mrq', items: (projection: DispatcherProjection) => projection.items.mrqs },
  batches: { title: 'Пакеты MRQ', itemKind: 'batch', items: (projection: DispatcherProjection) => projection.items.batches },
  approvals: { title: 'Ожидают одобрения', itemKind: 'proposal', items: (projection: DispatcherProjection) => projection.items.proposals.filter((item) => item.kind === 'approval') },
  decisions: { title: 'Решения', itemKind: 'decision', items: (projection: DispatcherProjection) => projection.items.decisions },
} as const;

function projectionEntityView(selection: DispatcherSelection | null, projection: DispatcherProjection): Record<string, unknown> | null {
  if (!selection || selection.kind === 'circuit') return null;
  if (selection.kind === 'role' || selection.kind === 'slot' || selection.kind === 'invocation') {
    const role = projection.agent_phases?.find((phase) => phase.phase_id === selection.phaseId)?.roles.find((item) => item.role_id === selection.roleId);
    if (!role) return null;
    if (selection.kind === 'role') return {
      phase: selection.phaseId, role: role.role_id, profile: role.agent_profile, model: role.model,
      reasoning_effort: role.reasoning_effort, configured_slots: role.configured_slots,
      environment: role.environment_status || role.environment_preset,
      requested: role.requested, running: role.running, queued: role.queued, completed: role.completed,
      failed: role.failed, cancelled: role.cancelled, interrupted: role.interrupted,
      slots: role.slots ?? [...new Map(role.invocations.map((invocation) => [invocation.slot_id, {
        slot_id: invocation.slot_id,
        display_label: invocation.slot_id,
        state: invocation.status === 'running' ? 'running' : 'idle',
        current_invocation_id: invocation.invocation_id,
      }])).values()],
      invocations: role.invocations,
      run_id: role.run_id,
      invocation_total: role.invocation_total ?? role.invocations.length,
      invocation_omitted: role.invocation_omitted ?? 0,
    };
    if (selection.kind === 'slot') {
      const slot = role.slots?.find((item) => item.slot_id === selection.slotId);
      return slot ? { ...slot, idle_reason: slot.idle_reason_code ? IDLE_REASON[slot.idle_reason_code] || slot.idle_reason_code : undefined } : null;
    }
    const invocation = role.invocations.find((item) => item.invocation_id === selection.invocationId);
    return invocation ? { ...invocation } : null;
  }
  if (selection.kind === 'queue') {
    if (!projection.circuits.some((circuit) => circuit.id === selection.circuitId)) return null;
    const collection = COLLECTIONS[selection.queueId as keyof typeof COLLECTIONS];
    if (!collection) return null;
    const items = collection.items(projection);
    const aggregate = projection.queue_aggregates?.[selection.queueId];
    const total = selection.queueId === 'approvals' ? projection.items.approval_count : aggregate?.total;
    return {
      queue_id: selection.queueId,
      title: collection.title,
      item_kind: collection.itemKind,
      total,
      visible: aggregate?.visible ?? items.length,
      omitted: aggregate?.omitted ?? (total === undefined ? undefined : Math.max(total - items.length, 0)),
      aggregate_available: total !== undefined,
      items,
    };
  }
  const items = selection.itemKind === 'dif'
    ? [...projection.items.dif_queue, ...projection.items.meaning_diffs, ...projection.items.noise_diffs]
    : selection.itemKind === 'mrq' ? projection.items.mrqs
    : selection.itemKind === 'outcome' ? projection.items.mrq_outcomes
    : selection.itemKind === 'batch' ? projection.items.batches
    : selection.itemKind === 'decision' ? projection.items.decisions
    : projection.items.proposals;
  return (items.find((item) => item.id === selection.itemId) as Record<string, unknown> | undefined) ?? null;
}

function DetailRows({ value }: { value: Record<string, unknown> }) {
  return <Stack component="dl" spacing={0.5} sx={{ m: 0 }}>
    {Object.entries(value).map(([key, item]) => <Box key={key}>
      <Typography component="dt" variant="caption" color="text.secondary">{key}</Typography>
      <Typography component="dd" variant="body2" sx={{ m: 0, overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>
        {item == null ? 'недоступно' : typeof item === 'object' ? JSON.stringify(item) : String(item)}
      </Typography>
    </Box>)}
  </Stack>;
}

function useDispatcherInspection(projectId: string, selection: DispatcherSelection | null, refreshToken: number) {
  const [resolvedByKey, setResolvedByKey] = useState<Record<string, DispatcherInspection>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const requestRef = useRef(0);
  const pageRequestRef = useRef(0);
  const pageControllerRef = useRef<AbortController | null>(null);
  const activeKeyRef = useRef('');
  const projectRef = useRef(projectId);
  activeKeyRef.current = selection ? `${projectId}:${selectionKey(selection)}` : '';
  useEffect(() => {
    if (projectRef.current === projectId) return;
    projectRef.current = projectId;
    pageControllerRef.current?.abort();
    setResolvedByKey({});
    setError('');
    setLoading(false);
    requestRef.current += 1;
    pageRequestRef.current += 1;
  }, [projectId]);
  useEffect(() => {
    if (!selection || (selection.kind !== 'slot' && selection.kind !== 'invocation')) return;
    const controller = new AbortController();
    const request = ++requestRef.current;
    const key = `${projectId}:${selectionKey(selection)}`;
    const params = new URLSearchParams({ kind: selection.kind });
    if (selection.kind === 'invocation') params.set('invocation_id', selection.invocationId);
    else {
      params.set('phase_id', selection.phaseId);
      params.set('role_id', selection.roleId);
      params.set('slot_id', selection.slotId);
      if (selection.runId) params.set('run_id', selection.runId);
    }
    setLoading(true);
    setError('');
    api<DispatcherInspection>(`/projects/${projectId}/dispatcher/inspect?${params}`, { signal: controller.signal })
      .then((value) => {
        if (request === requestRef.current && key === activeKeyRef.current) setResolvedByKey((cache) => {
          const current = cache[key];
          const merge = (previous?: InspectionPage<Record<string, unknown>>, next?: InspectionPage<Record<string, unknown>>) => {
            if (!previous || !next || previous.items.length <= next.items.length) return next;
            const items = [...next.items, ...previous.items.filter((item) => !next.items.some((candidate) => JSON.stringify(candidate) === JSON.stringify(item)))];
            return { ...next, items, next_cursor: previous.next_cursor, truncated: previous.truncated || next.truncated };
          };
          return {
            ...cache,
            [key]: current
              ? { ...value, history: merge(current.history, value.history), events: merge(current.events, value.events) }
              : value,
          };
        });
      })
      .catch((caught) => {
        if (!controller.signal.aborted && request === requestRef.current) {
          setError(caught instanceof Error ? caught.message : 'Не удалось загрузить сведения');
        }
      })
      .finally(() => {
        if (request === requestRef.current) setLoading(false);
      });
    return () => controller.abort();
  }, [projectId, selection && selectionKey(selection), refreshToken]);
  useEffect(() => {
    pageControllerRef.current?.abort();
    pageRequestRef.current += 1;
  }, [selection && selectionKey(selection)]);
  useEffect(() => () => pageControllerRef.current?.abort(), []);
  const resolved = activeKeyRef.current ? resolvedByKey[activeKeyRef.current] : undefined;
  const loadPage = useCallback(async (section: 'history' | 'events') => {
    if (!selection || (selection.kind !== 'slot' && selection.kind !== 'invocation') || !resolved) return;
    const cursor = resolved[section]?.next_cursor;
    if (!cursor) return;
    pageControllerRef.current?.abort();
    const controller = new AbortController();
    pageControllerRef.current = controller;
    const request = ++pageRequestRef.current;
    const activeKey = `${projectId}:${selectionKey(selection)}`;
    const params = new URLSearchParams({ kind: selection.kind, [section === 'events' ? 'event_cursor' : 'history_cursor']: cursor });
    if (selection.kind === 'invocation') params.set('invocation_id', selection.invocationId);
    else {
      params.set('phase_id', selection.phaseId);
      params.set('role_id', selection.roleId);
      params.set('slot_id', selection.slotId);
      if (selection.runId) params.set('run_id', selection.runId);
    }
    try {
      const page = await api<DispatcherInspection>(`/projects/${projectId}/dispatcher/inspect?${params}`, { signal: controller.signal });
      if (controller.signal.aborted || request !== pageRequestRef.current || activeKey !== activeKeyRef.current) return;
      setResolvedByKey((cache) => {
        const current = cache[activeKey];
        return current ? {
          ...cache,
          [activeKey]: {
          ...current,
          [section]: {
            ...page[section],
            items: [...(current[section]?.items ?? []), ...(page[section]?.items ?? [])],
          },
        },
        } : cache;
      });
    } catch (caught) {
      if (!controller.signal.aborted && request === pageRequestRef.current) setError(caught instanceof Error ? caught.message : 'Не удалось загрузить страницу');
    }
  }, [projectId, resolved, selection && selectionKey(selection)]);
  return { resolved: resolved ?? null, stale: false, error, loading, loadPage };
}

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

const aggregateValue = (value: number | string | boolean | undefined) =>
  value === undefined ? 'не определено' : typeof value === 'boolean' ? value ? 'да' : 'нет' : String(value);

function SplitStageProgress({ circuitId, projection }: { circuitId: 'analyze-dif' | 'form-mrq'; projection: DispatcherProjection }) {
  const aggregates = projection.circuits.find((item) => item.id === circuitId)?.aggregates ?? {};
  if (circuitId === 'analyze-dif') {
    const current = Number(aggregates.current_window_completed ?? 0);
    const total = Number(aggregates.current_window_total ?? 0);
    return <Paper component="section" variant="outlined" sx={{ p: 1.4 }} aria-label="Ход анализа DIF">
      <Typography variant="subtitle2" fontWeight={700}>Классификация DIF</Typography>
      <Typography variant="body2">
        Всего: {aggregateValue(aggregates.total)} · классифицировано: {aggregateValue(aggregates.classified)} · осталось: {aggregateValue(aggregates.remaining)}
      </Typography>
      <Typography variant="body2">
        Смысловые: {aggregateValue(aggregates.meaning)} · кандидаты в технический шум: {aggregateValue(aggregates.noise_candidate)}
      </Typography>
      <Typography variant="body2">
        Текущее окно: {current} из {total} · ошибки: {aggregateValue(aggregates.failed)} · пригодно для явного восстановления: {aggregateValue(aggregates.reusable_completed)}
      </Typography>
      {total > 0 && <LinearProgress sx={{ mt: 1 }} variant="determinate" value={Math.min(100, 100 * current / total)} aria-label="Прогресс текущего окна DIF" />}
      {aggregates.window_published === false && <Alert severity="warning" sx={{ mt: 1 }}>Текущее окно не опубликовано. Каноническое состояние не изменено.</Alert>}
    </Paper>;
  }
  const blocked = Boolean(aggregates.blocker_code);
  return <Paper component="section" variant="outlined" sx={{ p: 1.4 }} aria-label="Ход консолидации MRQ">
    <Typography variant="subtitle2" fontWeight={700}>Глобальная консолидация</Typography>
    <Typography variant="body2">
      Сохранено: {aggregateValue(aggregates.retained)} · новых: {aggregateValue(aggregates.new)} · объединено: {aggregateValue(aggregates.merged)} · разделено: {aggregateValue(aggregates.split)} · заменено: {aggregateValue(aggregates.superseded)}
    </Typography>
    <Typography variant="body2">
      Доказательства: {aggregateValue(aggregates.evidence)} · покрытие: {aggregateValue(aggregates.coverage)} · ожидают одобрения: {aggregateValue(aggregates.approval_pending)} · опубликовано: {aggregateValue(aggregates.published)}
    </Typography>
    <Typography variant="body2">
      Предварительный расчёт: разделов {aggregateValue(aggregates.partition_count)}, пар {aggregateValue(aggregates.pair_count)}, вызовов {aggregateValue(aggregates.planned_invocation_count)}
    </Typography>
    <Typography variant="body2">
      Фактическая ёмкость модели: {aggregateValue(aggregates.input_context_tokens)} токенов · оценщик: {aggregateValue(aggregates.context_estimator_version)}
    </Typography>
    {aggregates.plan_fingerprint && <Typography variant="caption" display="block">План: {aggregates.plan_fingerprint} · состояние: {aggregateValue(aggregates.plan_status)}</Typography>}
    {aggregates.already_applied && <Alert severity="success" sx={{ mt: 1 }}>Этот план уже применён; возвращён существующий результат без нового поколения.</Alert>}
    {Number(aggregates.legacy_decisions_stale ?? 0) > 0 && <Alert severity="warning" sx={{ mt: 1 }}>
      Устаревшие решения прежнего поколения: {aggregates.legacy_decisions_stale}. Они не перенесены и требуют нового решения.
    </Alert>}
    {blocked && <Alert severity="error" sx={{ mt: 1 }}>
      {aggregates.blocker_code}: {aggregates.blocker_message || 'Выполнение заблокировано'}
      {Number(aggregates.uncovered_partition_count ?? 0) > 0 ? ` · непокрытых разделов: ${aggregates.uncovered_partition_count}` : ''}
    </Alert>}
  </Paper>;
}

export function DispatcherPanel({ projectId, projection, fingerprint, selection, refreshToken = 0, onSelect, onClose, onOpenSources, onOpenIndexes, onOpenSettings, onOpenJournal, onOpenRegistry, readOnly = false }: { projectId: string; projection: DispatcherProjection; fingerprint: string; selection: DispatcherSelection | null; refreshToken?: number; onSelect?: (selection: DispatcherSelection, initiator: HTMLElement) => void; onClose: () => void; readOnly?: boolean } & ContextActions) {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<DispatcherOutcome | null>(null);
  const [plan, setPlan] = useState<RecomputePlan | null>(null);
  const [confirmations, setConfirmations] = useState<string[]>([]);
  const [recomputeRun, setRecomputeRun] = useState<RecomputeRun | null>(null);
  const [error, setError] = useState('');
  const [retryOpen, setRetryOpen] = useState(false);
  const [policySource, setPolicySource] = useState<'reuse-snapshot' | 'current-policy'>('reuse-snapshot');
  const [predecessorRunId, setPredecessorRunId] = useState('');
  const selectedCircuit = selection?.circuitId ?? null;
  const inspection = useDispatcherInspection(projectId, selection, refreshToken);
  const projectedView = projectionEntityView(selection, projection);
  const [lastViews, setLastViews] = useState<Record<string, Record<string, unknown>>>({});
  const currentSelectionKey = selection ? selectionKey(selection) : '';
  useEffect(() => setLastViews({}), [projectId]);
  useEffect(() => {
    if (projectedView && currentSelectionKey) setLastViews((views) => ({ ...views, [currentSelectionKey]: projectedView }));
  }, [currentSelectionKey, projectedView && JSON.stringify(projectedView)]);
  const entityView = projectedView ?? lastViews[currentSelectionKey] ?? null;
  const entityStale = Boolean(!projectedView && entityView);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  useEffect(() => { if (selection) headingRef.current?.focus(); }, [selection && selectionKey(selection)]);
  const job = selectedCircuit === 'analyze-dif' ? 'analyze-dif' : selectedCircuit === 'form-mrq' ? 'consolidate-mrq' : selectedCircuit === 'classify-mrq' ? 'classify-mrq' : selectedCircuit === 'decide-target' ? 'decide-mrq' : null;
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
  const pendingApproval = job === 'consolidate-mrq' || job === 'decide-mrq'
    ? projection.items.proposals.find((proposal) => proposal.job_id === job && proposal.kind === 'approval')
    : undefined;
  const retryCandidates = job ? (projection.retry_candidates || []).filter((item) => item.job_id === job) : [];
  const circuitReady = projection.circuits.find((item) => item.id === selectedCircuit)?.state === 'ready';
  const actions = leaseActionMatrix(lease, circuitReady, recomputeActive, Boolean(pendingApproval));
  const circuitAggregates = projection.circuits.find((item) => item.id === selectedCircuit)?.aggregates;
  if (circuitAggregates?.blocker_code) actions.approve = false;
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
      const action = job === 'consolidate-mrq' ? 'approve-consolidation' : 'approve-decision';
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
  const title = selection?.kind === 'role' ? `Роль ${selection.roleId}`
    : selection?.kind === 'slot' ? `Слот ${selection.slotId}`
      : selection?.kind === 'invocation' ? `Вызов ${selection.invocationId}`
        : selection?.kind === 'queue' ? 'Очередь'
          : selection?.kind === 'item' ? `${selection.itemKind.toUpperCase()} ${selection.itemId}`
              : selectedCircuit ? CIRCUIT_LABEL[selectedCircuit] : '';
  return (
    <Paper component="aside" role="complementary" aria-label="Сведения диспетчера" elevation={8} className="nodrag nowheel" sx={{ position: 'absolute', zIndex: 5, right: 16, bottom: 16, width: 500, maxWidth: 'calc(100% - 32px)', maxHeight: 'calc(100% - 32px)', overflowY: 'auto', p: 2 }}>
    <Stack spacing={1.4}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" alignItems="center"><Typography ref={headingRef} tabIndex={-1} component="h2" variant="subtitle1" fontWeight={700} flex={1}>{title}</Typography><Button size="small" onClick={onClose}>Закрыть</Button></Stack>
      {inspection.loading && <LinearProgress aria-label="Загрузка сведений" />}
      {inspection.error && <Alert severity="warning">{inspection.error}</Alert>}
      {(selectedCircuit === 'analyze-dif' || selectedCircuit === 'form-mrq') && <SplitStageProgress circuitId={selectedCircuit} projection={projection} />}
      {(selection?.kind === 'slot' || selection?.kind === 'invocation') && entityView && <Paper component="section" variant="outlined" sx={{ p: 1.4 }}>
        <Typography variant="subtitle2">Текущее состояние</Typography>
        <DetailRows value={entityView} />
      </Paper>}
      {(inspection.resolved || inspection.stale) && (selection?.kind === 'slot' || selection?.kind === 'invocation') && <Paper component="section" variant="outlined" sx={{ p: 1.4 }}>
        {inspection.stale && <Alert severity="warning">Показаны последние доступные сведения</Alert>}
        {inspection.resolved?.slot && <><Typography variant="subtitle2">Слот</Typography><DetailRows value={inspection.resolved.slot} /></>}
        {inspection.resolved?.invocation && <><Typography variant="subtitle2" mt={1}>Вызов</Typography><DetailRows value={inspection.resolved.invocation} /></>}
        {inspection.resolved?.history && <Stack component="section" spacing={0.5} mt={1}>
          <Typography variant="subtitle2">История ({inspection.resolved.history.items.length}{inspection.resolved.history.truncated ? ', показана частично' : ''})</Typography>
          {inspection.resolved.history.items.map((item, index) => <Paper variant="outlined" sx={{ p: 0.7 }} key={String(item.invocation_id ?? index)}><DetailRows value={item} /></Paper>)}
          {!inspection.resolved.history.available && !inspection.resolved.history.items.length && <Typography color="text.secondary">История недоступна</Typography>}
        </Stack>}
        {inspection.resolved?.events && <Stack component="section" spacing={0.5} mt={1}>
          <Typography variant="subtitle2">События ({inspection.resolved.events.items.length}{inspection.resolved.events.truncated ? ', показаны частично' : ''})</Typography>
          {inspection.resolved.events.items.map((item, index) => <Paper variant="outlined" sx={{ p: 0.7 }} key={String(item.sequence ?? index)}><DetailRows value={item} /></Paper>)}
          {!inspection.resolved.events.available && !inspection.resolved.events.items.length && <Typography color="text.secondary">События недоступны</Typography>}
        </Stack>}
        {inspection.resolved?.result && <><Typography variant="subtitle2" mt={1}>Результат</Typography><DetailRows value={inspection.resolved.result} /></>}
        <Stack direction="row" spacing={1}>
          {inspection.resolved?.history?.next_cursor && <Button onClick={() => void inspection.loadPage('history')}>Ещё история</Button>}
          {inspection.resolved?.events?.next_cursor && <Button onClick={() => void inspection.loadPage('events')}>Ещё события</Button>}
        </Stack>
        {selection.kind === 'invocation' && onOpenJournal && <Button onClick={() => onOpenJournal({ invocationId: selection.invocationId, runId: selection.runId })}>Открыть в журнале</Button>}
      </Paper>}
      {entityStale && <Alert severity="warning">Сущность больше не присутствует; показаны последние доступные сведения</Alert>}
      {(selection?.kind === 'queue') && entityView && <Paper component="section" variant="outlined" sx={{ p: 1.4 }}>
        <Typography variant="subtitle2" fontWeight={700}>{String(entityView.title)}</Typography>
        <Typography variant="caption" color="text.secondary">
          В текущем окне: {String(entityView.visible)}
        </Typography>
        <Typography variant="caption" display="block" color="text.secondary">
          {entityView.aggregate_available
            ? `Всего: ${String(entityView.total)} · не показано: ${String(entityView.omitted)}`
            : 'Точный размер очереди сервером не предоставлен'}
        </Typography>
        <Stack spacing={1} mt={1}>
          {((entityView.items as Array<{ id: string; path?: string; title?: string; state?: string; evidence_count?: number }>) ?? []).map((item) => <Box key={item.id}>
            <Button onClick={(event) => {
              const next: DispatcherSelection = {
                kind: 'item',
                itemKind: entityView.item_kind as 'dif' | 'mrq' | 'outcome' | 'batch' | 'decision' | 'proposal',
                circuitId: selection.circuitId,
                queueId: selection.queueId,
                itemId: item.id,
              };
              event.stopPropagation();
              onSelect?.(next, event.currentTarget);
            }}>{item.id}</Button>
            <Typography variant="caption" display="block" sx={{ overflowWrap: 'anywhere' }}>{item.path || item.title}</Typography>
            <Typography variant="caption" color="text.secondary">Состояние: {item.state || 'не определено'} · доказательств: {item.evidence_count ?? 0}</Typography>
            <Divider sx={{ mt: 1 }} />
          </Box>)}
          {!((entityView.items as unknown[]) ?? []).length && <Typography variant="body2" color="text.secondary">Элементов пока нет</Typography>}
        </Stack>
      </Paper>}
      {selection?.kind === 'item' && entityView && <Paper component="section" variant="outlined" sx={{ p: 1.4 }}>
        <DetailRows value={entityView} />
        {onOpenRegistry && selection.itemKind !== 'batch' && selection.itemKind !== 'proposal' && <Button onClick={() => onOpenRegistry({ registry: selection.itemKind === 'dif' ? 'diff-inventory' : 'mrq', itemId: selection.itemId })}>Открыть запись реестра</Button>}
      </Paper>}
      {selection?.kind === 'role' && entityView && <Paper component="section" variant="outlined" sx={{ p: 1.4 }}>
        <Typography>Профиль: {String(entityView.profile || 'не определён')}</Typography>
        <Typography>Модель: {String(entityView.model || 'не определена')}</Typography>
        <Typography>Уровень рассуждения: {String(entityView.reasoning_effort || 'не определён')}</Typography>
        <Typography>Одновременность: {String(entityView.configured_slots)}</Typography>
        <Typography>Окружение: {String(entityView.environment || 'не определено')}</Typography>
        <Typography>Запрошено: {String(entityView.requested)} · выполняется: {String(entityView.running)} · ожидает: {String(entityView.queued)} · завершено: {String(entityView.completed)} · ошибки: {String(entityView.failed)}</Typography>
        <Typography>Вызовов: {String(entityView.invocation_total)} · не показано: {String(entityView.invocation_omitted)}</Typography>
        <Typography variant="subtitle2" mt={1}>Все слоты</Typography>
        <Stack alignItems="flex-start">{((entityView.slots as Array<{ slot_id: string; display_label: string; state: string; idle_reason_code?: string; current_invocation_id?: string | null }>) ?? []).map((slot) =>
          <Stack key={slot.slot_id} direction="row" alignItems="center">
            <Button size="small" onClick={(event) => onSelect?.({
              kind: 'slot',
              circuitId: selection.circuitId,
              phaseId: selection.phaseId,
              roleId: selection.roleId,
              slotId: slot.slot_id,
              ...(typeof entityView.run_id === 'string' ? { runId: entityView.run_id } : {}),
            }, event.currentTarget)}>{slot.display_label}</Button>
            <Typography variant="body2">· {slot.state === 'idle' ? IDLE_REASON[slot.idle_reason_code || ''] || slot.idle_reason_code || 'Нет назначенной работы' : 'Выполняется'}</Typography>
            {slot.current_invocation_id && typeof entityView.run_id === 'string' && <Button size="small" onClick={(event) => onSelect?.({
              kind: 'invocation',
              circuitId: selection.circuitId,
              phaseId: selection.phaseId,
              roleId: selection.roleId,
              slotId: slot.slot_id,
              runId: entityView.run_id as string,
              invocationId: slot.current_invocation_id!,
            }, event.currentTarget)}>Открыть вызов</Button>}
          </Stack>)}</Stack>
        <Typography variant="subtitle2" mt={1}>Последние вызовы</Typography>
        <Stack alignItems="flex-start">{((entityView.invocations as Array<{ invocation_id: string; slot_id: string; status: string }>) ?? []).map((invocation) =>
          <Button key={invocation.invocation_id} size="small" onClick={(event) => onSelect?.({
            kind: 'invocation',
            circuitId: selection.circuitId,
            phaseId: selection.phaseId,
            roleId: selection.roleId,
            slotId: invocation.slot_id,
            invocationId: invocation.invocation_id,
            ...(typeof entityView.run_id === 'string' ? { runId: entityView.run_id } : {}),
          }, event.currentTarget)}>
            Вызов {invocation.invocation_id} · {invocation.status}
          </Button>)}</Stack>
      </Paper>}
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
          {pendingApproval && <Button color="success" variant="contained" disabled={readOnly || busy || !actions.approve} onClick={() => void approve()}>Одобрить предложение</Button>}
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
    ['Формирование и консолидация MRQ', '#6d3be7'],
    ['Формирование пакетов', '#7b1fa2'],
    ['Исследование цели', '#0097a7'],
  ] as const;
  const runningJob = Object.entries(projection.jobs).find(([, lease]) => lease.state === 'running')?.[0];
  const readyCircuit = projection.circuits.find((circuit) => circuit.state === 'ready' || circuit.state === 'blocked')?.id;
  const active = runningJob === 'decide-mrq' || readyCircuit === 'decide-target' ? 4
    : runningJob === 'classify-mrq' || readyCircuit === 'classify-mrq' ? 3
    : runningJob === 'consolidate-mrq' || readyCircuit === 'form-mrq' ? 2
      : runningJob === 'analyze-dif' ? 1
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
  onOpenJournal?: (target?: JournalTarget) => void;
  onOpenRegistry?: (target: RegistryTarget) => void;
}

export function PipelineDispatcher({ projectId, initialProjection, initialFingerprint, onOpenSources, onOpenIndexes, onOpenSettings, onOpenJournal, onOpenRegistry }: PipelineDispatcherProps) {
  const { projection, fingerprint, resyncing, error, lastEvent, reconciliationToken, refresh } = useDispatcherStream({ projectId, initialProjection, initialFingerprint });
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>([]);
  const [selection, setSelection] = useState<DispatcherSelection | null>(null);
  const [liveMessage, setLiveMessage] = useState('');
  const [viewport, setViewport] = useState<Viewport>({ x: 4, y: 10, zoom: 0.9 });
  const shellRef = useRef<HTMLDivElement | null>(null);
  const initiatorRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!lastEvent) return;
    setSelection((current) => {
      if (!current || current.kind !== 'slot' || current.runId) return current;
      const phaseId = String(lastEvent.phase_id || lastEvent.payload?.phase_id || '');
      const roleId = String(lastEvent.role_id || lastEvent.payload?.role_id || '');
      const slotId = String(lastEvent.slot_id || lastEvent.payload?.slot_id || '');
      const runId = String(lastEvent.run_id || lastEvent.payload?.run_id || '');
      return phaseId === current.phaseId && roleId === current.roleId && slotId === current.slotId && runId
        ? { ...current, runId }
        : current;
    });
  }, [lastEvent?.sequence]);
  useEffect(() => {
    if (!lastEvent || !selection) return;
    const invocationId = String(lastEvent.invocation_id || lastEvent.payload?.invocation_id || '');
    const slotId = String(lastEvent.slot_id || lastEvent.payload?.slot_id || '');
    const related = selection.kind === 'invocation' ? invocationId === selection.invocationId
      : selection.kind === 'slot' ? slotId === selection.slotId
        : selection.kind === 'circuit' || selection.kind === 'role' || selection.kind === 'queue';
    if (related) setLiveMessage(short(`${lastEvent.type || 'Обновление диспетчера'}: ${String(lastEvent.payload?.invocation_status || lastEvent.payload?.status || '')}`, 160));
  }, [lastEvent?.sequence, selection && selectionKey(selection)]);
  useEffect(() => {
    void api<{ events: RecentEvent[]; snapshot?: { events: RecentEvent[] }[] }>(`/projects/${projectId}/events?cursor=0&limit=50`).then((value) => {
      const events = value.events.length ? value.events : (value.snapshot ?? []).flatMap((item) => item.events ?? []);
      setRecentEvents(events.filter((event) => event.payload.kind?.startsWith('dispatcher.')).slice(-4).reverse());
    }).catch(() => setRecentEvents([]));
  }, [projectId, projection.revision]);
  if (resyncing && !initialProjection && !error) {
    return <Stack alignItems="center" spacing={2}><CircularProgress /><Typography>Пересинхронизация операционного состояния&hellip;</Typography></Stack>;
  }
  const activate = (next: DispatcherSelection, initiator: HTMLElement) => {
    initiatorRef.current = initiator;
    setSelection(next);
  };
  const closePanel = () => {
    const circuit = selection?.circuitId;
    const initiator = initiatorRef.current;
    setSelection(null);
    setTimeout(() => {
      (initiator?.isConnected ? initiator : shellRef.current?.querySelector<HTMLElement>(`[data-dispatcher-nav="${circuit}"]`))?.focus();
    }, 0);
  };
  const selectedInvocationStatus = selection?.kind === 'invocation'
    ? projection.agent_phases?.find((phase) => phase.phase_id === selection.phaseId)?.roles
      .find((role) => role.role_id === selection.roleId)?.invocations
      .find((invocation) => invocation.invocation_id === selection.invocationId)?.status
    : undefined;
  const terminalSelection = Boolean(selectedInvocationStatus && ['completed', 'failed', 'cancelled', 'interrupted'].includes(selectedInvocationStatus));
  return (
    <Stack spacing={1} ref={shellRef}>
      <Box aria-live="polite" aria-atomic="true" sx={{ position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clip: 'rect(0 0 0 0)' }}>{liveMessage}</Box>
      {error && <Alert severity="warning" action={resyncing ? <Button color="inherit" onClick={() => void refresh()}>Повторить снимок</Button> : undefined}>{error}</Alert>}
      {resyncing && !error && <LinearProgress aria-label="Пересинхронизация операционного состояния" />}
      <Stack direction="row" alignItems="center" spacing={2}><StageRail projection={projection} /><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={freshnessLabel(projection) === 'данные несвежие' ? 'warning' : 'success'} label={freshnessLabel(projection)} /><Typography variant="caption" color="text.secondary">Ревизия {projection.revision}</Typography>{onOpenJournal && <Button size="small" onClick={() => onOpenJournal()}>Журнал</Button>}{onOpenRegistry && <Button size="small" onClick={() => onOpenRegistry({ registry: 'diff-inventory', itemId: '' })}>Реестры</Button>}</Stack></Stack>
      <Stack direction="row" spacing={1} component="nav" aria-label="Этапы диспетчера" useFlexGap flexWrap="wrap">
        {(Object.keys(CIRCUIT_LABEL) as CircuitId[]).map((circuit) => (
          <Button
            key={circuit}
            size="small"
            data-dispatcher-nav={circuit}
            variant={selection?.circuitId === circuit ? 'contained' : 'outlined'}
            onClick={(event) => activate({ kind: 'circuit', circuitId: circuit }, event.currentTarget)}
          >
            {CIRCUIT_LABEL[circuit]}
          </Button>
        ))}
      </Stack>
      <ReactFlowProvider>
        <Box sx={{ position: 'relative' }} onKeyDown={(event) => {
          if (event.key === 'Escape' && selection) closePanel();
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
            selection={selection}
            refreshToken={(terminalSelection ? 0 : reconciliationToken) + (lastEvent && (
              selection?.kind === 'invocation'
                ? ((lastEvent.invocation_id || lastEvent.payload?.invocation_id) === selection.invocationId ? lastEvent.sequence : 0)
                : selection?.kind === 'slot'
                  ? ((lastEvent.phase_id || lastEvent.payload?.phase_id) === selection.phaseId && (lastEvent.role_id || lastEvent.payload?.role_id) === selection.roleId && (lastEvent.slot_id || lastEvent.payload?.slot_id) === selection.slotId ? lastEvent.sequence : 0)
                  : 0
            ) || 0)}
            onSelect={activate}
            onClose={closePanel}
            onOpenSources={onOpenSources}
            onOpenIndexes={onOpenIndexes}
            onOpenSettings={onOpenSettings}
            onOpenJournal={onOpenJournal}
            onOpenRegistry={onOpenRegistry}
            readOnly={resyncing}
          />
        </Box>
      </ReactFlowProvider>
      <DispatcherFooter projection={projection} events={recentEvents} />
    </Stack>
  );
}
