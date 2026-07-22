// Управляемая React Flow-мнемосхема диспетчера конвейера.
//
// Схема читает фиксированные узлы/рёбра и серверную проекцию; перетаскивание,
// соединение, удаление и сохранение компоновки отключены. Разрешены
// панорамирование, масштабирование, ``fitView`` и выбор узла для панели
// подробностей. ``prefers-reduced-motion`` отключает анимацию без потери данных.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Box, Button, Chip, CircularProgress, Divider, LinearProgress, Paper, Stack, Typography } from '@mui/material';
import { ArrowDownward, ArrowForward, Check, CheckCircle, DataObject, Hub, Psychology, Storage, TaskAlt, WarningAmber } from '@mui/icons-material';
import { Controls, Handle, Position, ReactFlow, ReactFlowProvider, type NodeProps, type NodeTypes } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { api, mutationHeaders } from '../api';
import { buildEdges, buildNodes, freshnessLabel, projectionStateColor, type DispatcherItems, type DispatcherProjection } from './projection';
import type { CircuitId } from './nodes';
import { useDispatcherStream } from './useDispatcherStream';

const CIRCUIT_LABEL: Record<CircuitId, string> = {
  'prepare-diffs': 'Подготовка различий',
  'analyze-dif': 'Анализ DIF',
  'form-mrq': 'Формирование MRQ',
  'decide-target': 'Исследование цели',
};

interface DispatcherNodeData extends Record<string, unknown> {
  title: string;
  subtitle?: string;
  circuit: CircuitId;
  circuitState: string;
  aggregates: Record<string, number | string>;
  lease?: { owner: string; renewed_at: string; state: string; work_unit_id: string };
  fresh: boolean;
  animate: boolean;
  isSelected: boolean;
  items: DispatcherItems;
}

function DispatcherNode({ data }: NodeProps) {
  const nodeData = data as DispatcherNodeData;
  const stateColor = projectionStateColor(nodeData.circuitState as never);
  return (
    <Box sx={{ height: '100%', overflow: 'hidden' }}>
      <Handle type="target" position={Position.Left} style={{ opacity: nodeData.circuit === 'prepare-diffs' ? 0 : 1 }} />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1.4, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Box sx={{ width: 28, height: 28, borderRadius: '50%', display: 'grid', placeItems: 'center', bgcolor: stateColor, color: '#fff', fontWeight: 700 }}>{['prepare-diffs', 'analyze-dif', 'form-mrq', 'decide-target'].indexOf(nodeData.circuit) + 1}</Box>
        <Typography variant="subtitle1" fontWeight={700} flex={1}>{nodeData.title}</Typography>
        <Chip size="small" label={stateLabel(nodeData.circuitState)} sx={{ color: stateColor, borderColor: stateColor }} variant="outlined" />
      </Stack>
      <Box sx={{ p: 1.5, height: 527, overflow: 'auto' }} className="nodrag nowheel">
        {nodeData.circuit === 'prepare-diffs' && <PrepareCircuit aggregates={nodeData.aggregates} items={nodeData.items} />}
        {nodeData.circuit === 'analyze-dif' && <AnalyzeCircuit aggregates={nodeData.aggregates} items={nodeData.items} lease={nodeData.lease} />}
        {nodeData.circuit === 'form-mrq' && <FormCircuit aggregates={nodeData.aggregates} items={nodeData.items} />}
        {nodeData.circuit === 'decide-target' && <DecideCircuit aggregates={nodeData.aggregates} items={nodeData.items} lease={nodeData.lease} />}
      </Box>
      <Handle type="source" position={Position.Right} style={{ opacity: nodeData.circuit === 'decide-target' ? 0 : 1 }} />
    </Box>
  );
}

const stateLabel = (state: string) => ({ complete: 'Готово', ready: 'В работе', blocked: 'Ожидает', unknown: 'Нет данных' }[state] ?? state);
export const shortWorkId = (value: string) => {
  const match = value.match(/^(DIF|MRQ|MRQB)-(.+)$/);
  return match && value.length > 14 ? `${match[1]}-${match[2].slice(0, 5)}` : value;
};
const short = (value: string, size = 22) => value.length > size ? `${value.slice(0, size)}…` : value;
const FlowDown = () => <ArrowDownward aria-hidden sx={{ alignSelf: 'center', color: 'primary.main', fontSize: 18, my: -0.6 }} />;

function StageCard({ icon, title, detail, complete = false }: { icon: React.ReactNode; title: string; detail: string; complete?: boolean }) {
  return <Paper variant="outlined" sx={{ p: 1.25 }}><Stack direction="row" spacing={1} alignItems="center"><Box color="primary.main">{icon}</Box><Box flex={1}><Typography variant="body2" fontWeight={700}>{title}</Typography><Typography variant="body2" color="text.secondary" fontSize={13}>{detail}</Typography></Box>{complete && <CheckCircle color="success" fontSize="small" />}</Stack></Paper>;
}

function Queue({ title, count, ids, color = 'primary.main', limit = 5 }: { title: string; count: number | string; ids: string[]; color?: string; limit?: number }) {
  return <Paper variant="outlined" sx={{ p: 1.2 }}><Stack direction="row" justifyContent="space-between" alignItems="center"><Typography variant="body2" fontWeight={700}>{title}</Typography><Typography variant="subtitle2" color={color}>{count}</Typography></Stack><Stack spacing={0.6} mt={1}>{ids.slice(0, limit).map((id) => <Box key={id} title={id} sx={{ px: 1, py: 0.6, border: '1px solid', borderColor: 'divider', borderRadius: 1, bgcolor: '#fff' }}><Typography variant="body2" color={color} fontWeight={700} noWrap>{shortWorkId(id)}</Typography></Box>)}{ids.length > limit && <Typography variant="body2" color="text.secondary">ещё {ids.length - limit}</Typography>}</Stack></Paper>;
}

function PrepareCircuit({ aggregates, items }: { aggregates: Record<string, number | string>; items: DispatcherItems }) {
  return <Stack spacing={1.1}><Typography variant="caption" color="text.secondary">Источники</Typography><Stack direction="row" spacing={0.7}>{['Типовая база', 'Рабочая база', 'Новая типовая'].map((name) => <Paper key={name} variant="outlined" sx={{ p: 1, flex: 1, textAlign: 'center' }}><Storage color="primary" fontSize="small" /><Typography display="block" variant="caption" fontWeight={650}>{name}</Typography><Typography variant="caption" color="text.secondary">Связана</Typography></Paper>)}</Stack><FlowDown /><StageCard icon={<DataObject />} title="Получение исходников" detail="Три поколения связаны" complete /><FlowDown /><StageCard icon={<Hub />} title="Индексация BSL" detail="Компонентные индексы" complete /><FlowDown /><StageCard icon={<TaskAlt />} title="Построение различий" detail={`Найдено: ${aggregates.diff_count ?? 0}`} complete /><FlowDown /><Queue title="Очередь DIF" count={items.dif_queue.length} ids={items.dif_queue.map((item) => item.id)} /></Stack>;
}

function WorkerCard({ name, current, state, active = false }: { name: string; current?: string; state: string; active?: boolean }) {
  const color: 'error' | 'primary' | 'success' | 'warning' = state === 'Ошибка' ? 'error' : active ? 'primary' : state === 'Свободен' ? 'success' : 'warning';
  return <Paper variant="outlined" sx={{ p: 1.1, minWidth: 0 }}><Stack direction="row" alignItems="center" spacing={0.7}><Psychology color={state === 'Ошибка' ? 'error' : active ? 'primary' : 'disabled'} fontSize="small" /><Typography variant="body2" fontWeight={700} flex={1} noWrap>{name}</Typography><Chip size="small" label={state} color={color} variant="outlined" /></Stack><Typography variant="body2" fontSize={13} color="text.secondary" title={current} noWrap>Текущий: {current ? shortWorkId(current) : '—'}</Typography><LinearProgress variant={active ? 'indeterminate' : 'determinate'} value={0} sx={{ mt: 0.8 }} /></Paper>;
}

function leasePresentation(lease: DispatcherNodeData['lease'] | undefined, runningLabel: string) {
  if (!lease) return { state: 'Свободен', active: false };
  if (lease.state === 'running' && Date.now() - Date.parse(lease.renewed_at) >= 30_000) return { state: 'Аренда истекла', active: false };
  if (lease.state === 'running') return { state: runningLabel, active: true };
  return { state: ({ failed: 'Ошибка', resumable: 'Остановлен', blocked: 'Ожидает одобрения', stale: 'Устарел' } as Record<string, string>)[lease.state] ?? lease.state, active: false };
}

function AnalyzeCircuit({ aggregates, items, lease }: { aggregates: Record<string, number | string>; items: DispatcherItems; lease?: DispatcherNodeData['lease'] }) {
  const queue = items.dif_queue.map((item) => item.id);
  const leaseView = leasePresentation(lease, 'Анализирует');
  return <Stack spacing={1.2}><Stack direction="row" spacing={1} alignItems="flex-start"><Box flex={0.8}><Queue title="Очередь DIF" count={aggregates.window_size ?? queue.length} ids={queue} limit={4} /></Box><ArrowForward aria-hidden sx={{ mt: 7, color: 'primary.main' }} /><Box flex={1.4}><Typography variant="body2" fontWeight={700} mb={0.7}>Исполнители (до 4)</Typography><Box display="grid" gridTemplateColumns="1fr 1fr" gap={0.8}>{[0, 1, 2, 3].map((index) => <WorkerCard key={index} name={`Агент-${index + 1}`} current={index === 0 && lease ? lease.work_unit_id : undefined} state={index === 0 ? leaseView.state : 'Свободен'} active={index === 0 && leaseView.active} />)}</Box></Box></Stack><FlowDown /><Stack direction="row" spacing={1}><Box flex={1}><Queue title="Смысловые различия" count={items.meaning_diffs.length} ids={items.meaning_diffs.map((item) => item.id)} limit={4} /></Box><Box flex={1}><Queue title="Технический шум" count={items.noise_diffs.length} ids={items.noise_diffs.map((item) => item.id)} color="text.secondary" limit={4} /></Box></Stack></Stack>;
}

function FormCircuit({ aggregates, items }: { aggregates: Record<string, number | string>; items: DispatcherItems }) {
  const proposals = [...items.proposals, ...Array(Math.max(0, 3 - items.proposals.length)).fill(null)].slice(0, 3);
  return <Stack spacing={1.2}><Stack direction="row" spacing={1} alignItems="flex-start"><Box width="22%"><Queue title="Смысловые DIF" count={items.meaning_diffs.length} ids={items.meaning_diffs.map((item) => item.id)} color="#6d3be7" limit={4} /></Box><ArrowForward aria-hidden sx={{ mt: 7, color: '#6d3be7' }} /><Stack width="37%" spacing={0.8}><Paper variant="outlined" sx={{ p: 1.1, textAlign: 'center' }}><Hub sx={{ color: '#6d3be7' }} /><Typography variant="body2" fontWeight={700}>Координатор MRQ</Typography><Typography variant="caption">Кандидатов: {items.proposals.length}</Typography></Paper>{proposals.map((item, index) => <Paper key={item?.id ?? `empty-${index}`} variant="outlined" sx={{ p: 1 }}><Typography variant="caption" fontWeight={700}>Группировщик-{index + 1}</Typography><Typography display="block" variant="caption" color={item ? '#6d3be7' : 'text.secondary'} title={item?.dif_ids.join(', ')}>{item ? short(item.dif_ids.join(', ') || item.semantic_key, 30) : 'Свободен · нет задания'}</Typography>{item && <LinearProgress variant="determinate" value={100} color="secondary" sx={{ mt: 0.7 }} />}</Paper>)}</Stack><ArrowForward aria-hidden sx={{ mt: 18, color: '#6d3be7' }} /><Stack width="32%" spacing={0.8}><Paper variant="outlined" sx={{ p: 1 }}><Typography variant="caption" fontWeight={700}>Предложения групп</Typography><Typography display="block" variant="caption">Сформировано: {items.proposals.length}</Typography></Paper><Paper variant="outlined" sx={{ p: 1 }}><Typography variant="caption" fontWeight={700}>Проверяющий агент</Typography><Typography display="block" variant="caption">Доказательств: {items.proposals.reduce((sum, item) => sum + item.evidence_count, 0)}</Typography></Paper><Paper variant="outlined" sx={{ p: 1, borderColor: items.proposals.length ? 'warning.main' : 'success.main' }}><Typography variant="caption" fontWeight={700}>Барьер полного покрытия</Typography><Typography display="block" variant="caption">Назначено DIF: {aggregates.disposition_count ?? 0}</Typography></Paper><Queue title="Исходные MRQ" count={items.mrqs.length} ids={items.mrqs.map((item) => item.id)} color="#6d3be7" limit={2} /></Stack></Stack><Divider /><Typography variant="caption" fontWeight={700}>Публикация и пакеты исследования цели</Typography><Stack direction="row" spacing={1} sx={{ overflowX: 'auto' }}><StageCard icon={<Hub />} title="Классификатор MRQ" detail={`Пакетов: ${items.batches.length}`} complete={items.batches.length > 0} /><StageCard icon={<TaskAlt />} title="Публикация" detail={`Готово MRQ: ${items.mrqs.length}`} complete={items.mrqs.length > 0} />{items.batches.slice(0, 4).map((batch) => <Paper key={batch.id} variant="outlined" title={batch.id} sx={{ p: 1, minWidth: 120 }}><Typography variant="caption" color="#6d3be7" fontWeight={700}>{shortWorkId(batch.id)}</Typography><Typography display="block" variant="caption">MRQ: {batch.mrq_ids.length}</Typography></Paper>)}</Stack></Stack>;
}

function DecideCircuit({ aggregates, items, lease }: { aggregates: Record<string, number | string>; items: DispatcherItems; lease?: DispatcherNodeData['lease'] }) {
  const pending = items.mrqs.filter((mrq) => !items.decisions.some((decision) => decision.id === mrq.id));
  const gaps = items.decisions.filter((item) => item.gap);
  const leaseView = leasePresentation(lease, 'Исследует');
  return <Stack spacing={1.1}><Box display="grid" gridTemplateColumns="0.8fr 1.65fr 0.75fr" gap={1} alignItems="start"><Queue title="Очередь MRQ" count={pending.length} ids={pending.map((item) => item.id)} color="#0097a7" limit={4} /><Box><Typography variant="body2" fontWeight={700} mb={0.7}>Исследователи (до 4)</Typography><Box display="grid" gridTemplateColumns="1fr 1fr" gap={0.8}>{[0, 1, 2, 3].map((index) => <WorkerCard key={index} name={`Исследователь-${index + 1}`} current={index === 0 && lease ? lease.work_unit_id : undefined} state={index === 0 ? leaseView.state : 'Свободен'} active={index === 0 && leaseView.active} />)}</Box></Box><Paper variant="outlined" sx={{ p: 1.2, textAlign: 'center', alignSelf: 'stretch', display: 'grid', placeContent: 'center' }}><Storage sx={{ color: '#0097a7', mx: 'auto' }} /><Typography variant="body2" fontWeight={700}>Целевая база</Typography><Typography variant="body2" fontSize={13} color="text.secondary">Связана</Typography></Paper></Box><Divider /><Typography variant="body2" fontWeight={700}>Результаты исследования цели</Typography>{items.decisions.slice(0, 3).map((item) => <Paper key={item.id} variant="outlined" sx={{ p: 1, borderColor: item.gap ? 'warning.main' : 'success.main' }}><Stack direction="row" alignItems="center" spacing={1}>{item.gap ? <WarningAmber color="warning" /> : <TaskAlt color="success" />}<Box flex={1} minWidth={0}><Typography variant="body2" fontWeight={700} title={item.id}>{shortWorkId(item.id)}</Typography><Typography display="block" variant="body2" fontSize={13} noWrap title={item.title || item.decision}>{item.title || item.decision}</Typography></Box><Chip size="small" label={decisionLabel(item.decision)} color={item.gap ? 'warning' : 'success'} variant="outlined" /></Stack></Paper>)}{items.decisions.length === 0 && <Alert severity="info">Решения ещё не утверждены</Alert>}<Stack direction="row" spacing={1}><Chip size="small" label={`Типовой функционал: ${Number(aggregates.decision_count ?? 0) - Number(aggregates.adapt_count ?? 0)}`} color="success" variant="outlined" /><Chip size="small" label={`Разрывы: ${gaps.length}`} color="warning" variant="outlined" /></Stack></Stack>;
}

const decisionLabel = (decision: string) => ({ adopt_vendor: 'Принять типовое', adapt: 'Адаптировать', retain_custom: 'Сохранить доработку', out_of_scope: 'Вне объёма' }[decision] ?? decision);

const NODE_TYPES: NodeTypes = { dispatcherNode: DispatcherNode };

interface DispatcherActionPayload {
  actor?: string;
  work_unit_id?: string;
  instruction_supplement?: string;
  timeout_seconds?: number;
  proposal_key?: string;
  payload?: Record<string, unknown>;
  expected_fingerprint?: string;
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

async function postDispatcherAction(projectId: string, jobId: CircuitId extends never ? string : 'discover-mrq' | 'decide-mrq', action: string, fingerprint: string, body: DispatcherActionPayload): Promise<DispatcherOutcome> {
  const response = await api<DispatcherActionResponse>(`/projects/${projectId}/dispatcher/${jobId}/${action}`, {
    method: 'POST',
    headers: mutationHeaders(),
    body: JSON.stringify({ ...body, expected_fingerprint: fingerprint }),
  });
  return response.outcome;
}

interface ContextActions { onOpenSources?: () => void; onOpenIndexes?: () => void; onOpenSettings?: () => void }

function DispatcherPanel({ projectId, projection, fingerprint, selectedCircuit, onClose, onOpenSources, onOpenIndexes, onOpenSettings }: { projectId: string; projection: DispatcherProjection; fingerprint: string; selectedCircuit: CircuitId | null; onClose: () => void } & ContextActions) {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<DispatcherOutcome | null>(null);
  const [error, setError] = useState('');
  const job = selectedCircuit === 'analyze-dif' || selectedCircuit === 'form-mrq' ? 'discover-mrq' : selectedCircuit === 'decide-target' ? 'decide-mrq' : null;
  const lease = job ? projection.jobs[job] : undefined;
  const pendingApproval = job ? projection.items.proposals.find((proposal) => proposal.job_id === job && proposal.kind === 'approval') : undefined;

  const run = useCallback(async (action: 'start' | 'stop' | 'resume' | 'cancel' | 'retry') => {
    if (!job) return;
    setBusy(true);
    setError('');
    try {
      const result = await postDispatcherAction(projectId, job, action, fingerprint, { actor: 'local-user' });
      setOutcome(result);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'dispatcher action failed');
    } finally {
      setBusy(false);
    }
  }, [job, projectId, fingerprint]);

  const approve = useCallback(async () => {
    if (!job || !pendingApproval) return;
    if (pendingApproval.noise_count > 0) {
      setError('Пакет содержит технический шум: сначала заполните доказательства и обоснование одобрения в реестре.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const action = job === 'discover-mrq' ? 'approve-batch' : 'approve-decision';
      const result = await postDispatcherAction(projectId, job, action, fingerprint, { actor: 'local-user', proposal_key: pendingApproval.id, payload: { approved_noise: [] } });
      setOutcome(result);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : 'approval failed');
    } finally {
      setBusy(false);
    }
  }, [job, pendingApproval, projectId, fingerprint]);

  if (!selectedCircuit) return null;
  return (
    <Paper elevation={8} className="nodrag nowheel" sx={{ position: 'absolute', zIndex: 5, right: 16, bottom: 16, width: 430, maxWidth: 'calc(100% - 32px)', p: 2 }}>
    <Stack spacing={1.4}>
      {error && <Alert severity="error">{error}</Alert>}
      <Stack direction="row" alignItems="center"><Typography variant="subtitle1" fontWeight={700} flex={1}>{CIRCUIT_LABEL[selectedCircuit]}</Typography><Button size="small" onClick={onClose}>Закрыть</Button></Stack>
      {selectedCircuit === 'prepare-diffs' ? <Stack direction="row" spacing={1} flexWrap="wrap">{onOpenSources && <Button variant="contained" onClick={onOpenSources}>Настроить источники</Button>}{onOpenIndexes && <Button variant="outlined" onClick={onOpenIndexes}>Проверить индексы</Button>}</Stack> : <><Typography variant="caption">Аренда: {lease ? `${lease.owner} (${lease.state})` : 'не захвачена'} · ревизия {projection.revision}</Typography><Stack direction="row" spacing={1} flexWrap="wrap">
        <Button variant="contained" disabled={busy} onClick={() => void run('start')}>Запустить</Button>
        <Button variant="outlined" disabled={busy} onClick={() => void run('stop')}>Мягкая остановка</Button>
        <Button variant="outlined" disabled={busy} onClick={() => void run('resume')}>Продолжить</Button>
        <Button color="warning" disabled={busy} onClick={() => void run('retry')}>Явный повтор</Button>
        <Button color="error" disabled={busy} onClick={() => void run('cancel')}>Отменить</Button>
        {pendingApproval && <Button color="success" variant="contained" disabled={busy} onClick={() => void approve()}>Одобрить предложение</Button>}
        {onOpenSettings && <Button onClick={onOpenSettings}>Профили и параметры</Button>}
      </Stack></>}
      {outcome && (
        <Alert severity={outcome.status === 'failed' || outcome.status === 'blocked' || outcome.status === 'stale' ? 'warning' : 'success'}>
          <Typography>Статус: {outcome.status}; ревизия: {outcome.revision}</Typography>
          {outcome.blocker && <Typography variant="caption">{outcome.blocker.code}: {outcome.blocker.message}</Typography>}
        </Alert>
      )}
      {job && <Typography variant="caption">Автоматический повтор отключён; повтор запускается только явно.</Typography>}
    </Stack>
    </Paper>
  );
}

function DispatcherCanvas({ projectId, projection, fingerprint, ...actions }: { projectId: string; projection: DispatcherProjection; fingerprint: string } & ContextActions) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [transitionPulse, setTransitionPulse] = useState(false);

  useEffect(() => {
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReducedMotion(media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (projection.revision === 0) return;
    setTransitionPulse(true);
    const timer = window.setTimeout(() => setTransitionPulse(false), 900);
    return () => clearTimeout(timer);
  }, [projection.revision]);

  const animate = !reducedMotion && transitionPulse;
  const nodes = useMemo(() => buildNodes(projection, selectedId, animate), [projection, selectedId, animate]);
  const edges = useMemo(() => buildEdges(animate), [animate]);
  const selectedFixed = selectedId ? circuitForNode(selectedId) : null;

  return (
    <Stack spacing={1}>
      <Box sx={{ position: 'relative', height: 625, border: '1px solid', borderColor: 'divider', borderRadius: 2, background: '#fbfcfe', overflow: 'hidden' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          panOnDrag
          zoomOnScroll
          defaultViewport={{ x: 10, y: 10, zoom: 0.96 }}
          minZoom={0.65}
          maxZoom={1.25}
          proOptions={{ hideAttribution: true }}
          onNodeClick={(_, node) => setSelectedId(node.id)}
        >
          <Controls showInteractive={false} />
        </ReactFlow>
        <DispatcherPanel projectId={projectId} projection={projection} fingerprint={fingerprint} selectedCircuit={selectedFixed} onClose={() => setSelectedId(null)} {...actions} />
      </Box>
    </Stack>
  );
}

function circuitForNode(nodeId: string): CircuitId | null {
  return ['prepare-diffs', 'analyze-dif', 'form-mrq', 'decide-target'].includes(nodeId) ? nodeId as CircuitId : null;
}

type RecentEvent = { sequence: number; timestamp: string; type: string; payload: { kind?: string; work_unit_id?: string } };

function StageRail({ projection }: { projection: DispatcherProjection }) {
  const states = Object.fromEntries(projection.circuits.map((circuit) => [circuit.id, circuit.state]));
  const stages = [
    ['Подготовка', '#1976d2'],
    ['Анализ DIF', '#1976d2'],
    ['Формирование MRQ', '#6d3be7'],
    ['Публикация', '#6d3be7'],
    ['Исследование цели', '#0097a7'],
    ['Результат цели', '#0097a7'],
  ] as const;
  const runningJob = Object.entries(projection.jobs).find(([, lease]) => lease.state === 'running')?.[0];
  const readyCircuit = projection.circuits.find((circuit) => circuit.state === 'ready' || circuit.state === 'blocked')?.id;
  const active = runningJob === 'decide-mrq' || readyCircuit === 'decide-target' ? 4
    : runningJob === 'discover-mrq' || readyCircuit === 'form-mrq' ? 2
      : readyCircuit === 'analyze-dif' ? 1
      : projection.items.decisions.length ? 5
        : projection.items.batches.length ? 4
          : projection.items.mrqs.length ? 3
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
  onOpenSettings?: () => void;
  onOpenJournal?: () => void;
  onOpenRegistries?: () => void;
}

export function PipelineDispatcher({ projectId, initialProjection, initialFingerprint, onOpenSources, onOpenIndexes, onOpenSettings, onOpenJournal, onOpenRegistries }: PipelineDispatcherProps) {
  const { projection, fingerprint, resyncing, error } = useDispatcherStream({ projectId, initialProjection, initialFingerprint });
  const [recentEvents, setRecentEvents] = useState<RecentEvent[]>([]);
  useEffect(() => {
    void api<{ events: RecentEvent[]; snapshot?: { events: RecentEvent[] }[] }>(`/projects/${projectId}/events?cursor=0&limit=50`).then((value) => {
      const events = value.events.length ? value.events : (value.snapshot ?? []).flatMap((item) => item.events ?? []);
      setRecentEvents(events.filter((event) => event.payload.kind?.startsWith('dispatcher.')).slice(-4).reverse());
    }).catch(() => setRecentEvents([]));
  }, [projectId, projection.revision]);
  if (resyncing) {
    return <Stack alignItems="center" spacing={2}><CircularProgress /><Typography>Пересинхронизация операционного состояния&hellip;</Typography></Stack>;
  }
  return (
    <Stack spacing={1}>
      {error && <Alert severity="warning">{error}</Alert>}
      <Stack direction="row" alignItems="center" spacing={2}><StageRail projection={projection} /><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={freshnessLabel(projection) === 'данные несвежие' ? 'warning' : 'success'} label={freshnessLabel(projection)} /><Typography variant="caption" color="text.secondary">Ревизия {projection.revision}</Typography>{onOpenJournal && <Button size="small" onClick={onOpenJournal}>Журнал</Button>}{onOpenRegistries && <Button size="small" onClick={onOpenRegistries}>Реестры</Button>}</Stack></Stack>
      <ReactFlowProvider>
        <DispatcherCanvas projectId={projectId} projection={projection} fingerprint={fingerprint} onOpenSources={onOpenSources} onOpenIndexes={onOpenIndexes} onOpenSettings={onOpenSettings} />
      </ReactFlowProvider>
      <DispatcherFooter projection={projection} events={recentEvents} />
    </Stack>
  );
}
