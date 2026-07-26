import { MarkerType, type Edge, type Node } from '@xyflow/react';
import {
  VISIBLE_DECISION_LIMIT,
  VISIBLE_INVOCATION_LIMIT,
  VISIBLE_QUEUE_LIMIT,
  roleProgress,
  visibleWindow,
  type AgentInvocationProjection,
  type CircuitId,
  type DispatcherProjection,
} from './projection';

export type DispatcherInteraction = 'open-circuit' | 'none';
export type DispatcherGraphNodeKind = 'stage' | 'zone' | 'agent';
type DispatcherPortOrdinal = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16;
export type DispatcherPortId =
  | 'left'
  | 'right'
  | 'top'
  | 'bottom'
  | 'bottom-left'
  | 'bottom-right'
  | 'top-left'
  | 'top-right'
  | `top-${DispatcherPortOrdinal}`
  | `left-${DispatcherPortOrdinal}`;
export interface DispatcherGraphPort {
  id: DispatcherPortId;
  type: 'source' | 'target';
}

export interface DispatcherGraphNodeData extends Record<string, unknown> {
  kind: DispatcherGraphNodeKind;
  circuitId: CircuitId;
  interaction: DispatcherInteraction;
  label: string;
  accent: string;
  state?: string;
  lines?: string[];
  items?: string[];
  invocations?: Array<{ id: string; workUnitId: string; state: string; updatedAt?: string }>;
  subzones?: Array<{ id: string; label: string; state: string }>;
  ports?: DispatcherGraphPort[];
  active?: boolean;
}

export interface DispatcherGraphModel {
  nodes: Node<DispatcherGraphNodeData>[];
  edges: Edge[];
}

type StageDefinition = {
  id: CircuitId;
  label: string;
  x: number;
  width: number;
  accent: string;
  background: string;
};

type ZoneDefinition = {
  id: string;
  circuitId: CircuitId;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
  accent: string;
  agentRole?: string;
};

export type DispatcherGraphLink = readonly [
  source: string,
  target: string,
  sourceHandle: DispatcherPortId,
  targetHandle: DispatcherPortId,
];

const STAGE_HEIGHT = 760;

export const DISPATCHER_GRAPH_STAGES: readonly StageDefinition[] = [
  { id: 'prepare-diffs', label: 'Подготовка различий', x: 0, width: 310, accent: '#1976d2', background: '#f4f8ff' },
  { id: 'analyze-dif', label: 'Анализ DIF', x: 330, width: 440, accent: '#1976d2', background: '#f7faff' },
  { id: 'form-mrq', label: 'Формирование MRQ', x: 790, width: 760, accent: '#6d3be7', background: '#faf8ff' },
  { id: 'classify-mrq', label: 'Формирование пакетов', x: 1570, width: 440, accent: '#7b1fa2', background: '#fcf7ff' },
  { id: 'decide-target', label: 'Исследование целевой базы', x: 2030, width: 500, accent: '#0097a7', background: '#f4fcfd' },
] as const;

export const DISPATCHER_GRAPH_ZONES: readonly ZoneDefinition[] = [
  { id: 'sources', circuitId: 'prepare-diffs', label: 'Источники', x: 16, y: 62, width: 278, height: 82, accent: '#1976d2' },
  { id: 'sources-acquire', circuitId: 'prepare-diffs', label: 'Получение исходников', x: 38, y: 170, width: 234, height: 72, accent: '#1976d2' },
  { id: 'diffs-build', circuitId: 'prepare-diffs', label: 'Построение различий', x: 38, y: 270, width: 234, height: 72, accent: '#1976d2' },
  { id: 'indexes-build', circuitId: 'prepare-diffs', label: 'Индексация BSL', x: 38, y: 370, width: 234, height: 72, accent: '#1976d2' },
  { id: 'prepare-dif-window', circuitId: 'prepare-diffs', label: 'Окно DIF', x: 16, y: 480, width: 278, height: 210, accent: '#1976d2' },

  { id: 'analyze-dif-window', circuitId: 'analyze-dif', label: 'Окно DIF', x: 16, y: 76, width: 112, height: 300, accent: '#1976d2' },
  { id: 'analyze-workers', circuitId: 'analyze-dif', label: 'Фактические анализаторы', x: 150, y: 76, width: 274, height: 300, accent: '#1976d2', agentRole: 'analyzer' },
  { id: 'analyze-meaning', circuitId: 'analyze-dif', label: 'Смысловые DIF', x: 16, y: 410, width: 194, height: 270, accent: '#1976d2' },
  { id: 'analyze-noise', circuitId: 'analyze-dif', label: 'Технический шум', x: 230, y: 410, width: 194, height: 270, accent: '#757575' },

  { id: 'form-meaning', circuitId: 'form-mrq', label: 'Смысловые DIF', x: 16, y: 76, width: 112, height: 260, accent: '#6d3be7' },
  { id: 'form-coordinator', circuitId: 'form-mrq', label: 'Координатор', x: 148, y: 76, width: 170, height: 260, accent: '#6d3be7', agentRole: 'coordinator' },
  { id: 'form-groupers', circuitId: 'form-mrq', label: 'Группировщики', x: 338, y: 76, width: 220, height: 260, accent: '#6d3be7', agentRole: 'grouper' },
  { id: 'form-proposals', circuitId: 'form-mrq', label: 'Предложения', x: 578, y: 76, width: 166, height: 120, accent: '#6d3be7' },
  { id: 'form-review', circuitId: 'form-mrq', label: 'Проверка', x: 578, y: 216, width: 166, height: 120, accent: '#6d3be7' },
  { id: 'form-barrier', circuitId: 'form-mrq', label: 'Барьер полного покрытия', x: 396, y: 370, width: 170, height: 112, accent: '#6d3be7' },
  { id: 'form-publication', circuitId: 'form-mrq', label: 'Публикация', x: 396, y: 646, width: 170, height: 66, accent: '#6d3be7' },
  { id: 'form-summary', circuitId: 'form-mrq', label: 'Сводка опубликованных MRQ', x: 16, y: 646, width: 360, height: 66, accent: '#6d3be7' },

  { id: 'classify-input', circuitId: 'classify-mrq', label: 'Исходные MRQ', x: 16, y: 76, width: 112, height: 300, accent: '#7b1fa2' },
  { id: 'classify-workers', circuitId: 'classify-mrq', label: 'Фактический классификатор', x: 148, y: 76, width: 276, height: 300, accent: '#7b1fa2', agentRole: 'classifier' },
  { id: 'classify-validation', circuitId: 'classify-mrq', label: 'Проверка покрытия', x: 16, y: 410, width: 180, height: 210, accent: '#7b1fa2' },
  { id: 'classify-batches', circuitId: 'classify-mrq', label: 'Пакеты исследования', x: 216, y: 410, width: 208, height: 210, accent: '#7b1fa2' },

  { id: 'decide-mrq-queue', circuitId: 'decide-target', label: 'Очередь MRQ', x: 16, y: 76, width: 112, height: 300, accent: '#0097a7' },
  { id: 'decide-researchers', circuitId: 'decide-target', label: 'Фактические исследователи', x: 148, y: 76, width: 220, height: 300, accent: '#0097a7', agentRole: 'researcher' },
  { id: 'decide-target-base', circuitId: 'decide-target', label: 'Целевая база', x: 388, y: 76, width: 96, height: 160, accent: '#0097a7' },
  { id: 'decide-approval', circuitId: 'decide-target', label: 'Ожидание одобрения', x: 388, y: 256, width: 96, height: 120, accent: '#0097a7' },
  { id: 'decide-outcomes', circuitId: 'decide-target', label: 'Исходы исследования', x: 16, y: 410, width: 278, height: 210, accent: '#0097a7' },
  { id: 'decide-summary', circuitId: 'decide-target', label: 'Сводка решений', x: 314, y: 410, width: 170, height: 210, accent: '#0097a7' },
] as const;

export const DISPATCHER_GRAPH_LINKS: readonly DispatcherGraphLink[] = [
  ['sources', 'sources-acquire', 'bottom', 'top'],
  ['sources-acquire', 'diffs-build', 'bottom', 'top'],
  ['diffs-build', 'indexes-build', 'bottom', 'top'],
  ['indexes-build', 'prepare-dif-window', 'bottom', 'top'],
  ['prepare-dif-window', 'analyze-dif-window', 'right', 'left'],
  ['analyze-dif-window', 'analyze-workers', 'right', 'left'],
  ['analyze-workers', 'analyze-meaning', 'bottom-left', 'top'],
  ['analyze-workers', 'analyze-noise', 'bottom-right', 'top'],
  ['analyze-meaning', 'form-meaning', 'right', 'left'],
  ['form-meaning', 'form-coordinator', 'right', 'left'],
  ['form-coordinator', 'form-groupers', 'right', 'left'],
  ['form-groupers', 'form-proposals', 'right', 'left'],
  ['form-proposals', 'form-review', 'bottom', 'top'],
  ['form-review', 'form-barrier', 'bottom-left', 'top'],
  ['form-barrier', 'form-publication', 'bottom', 'top'],
  ['form-publication', 'form-summary', 'left', 'top'],
  ['form-summary', 'classify-input', 'right', 'left'],
  ['classify-input', 'classify-workers', 'right', 'left'],
  ['classify-workers', 'classify-validation', 'bottom-left', 'top'],
  ['classify-validation', 'classify-batches', 'right', 'left'],
  ['classify-batches', 'decide-mrq-queue', 'right', 'left'],
  ['decide-mrq-queue', 'decide-researchers', 'right', 'left'],
  ['decide-researchers', 'decide-target-base', 'right', 'left'],
  ['decide-target-base', 'decide-approval', 'bottom', 'top'],
  ['decide-approval', 'decide-outcomes', 'bottom-left', 'top-right'],
  ['decide-outcomes', 'decide-summary', 'right', 'left'],
] as const;

export const DISPATCHER_AGENT_FLOWS = {
  'analyze-workers': { target: 'analyze-meaning', sourceHandle: 'bottom', targetSide: 'top', type: 'straight' },
  'form-coordinator': { target: 'form-groupers', sourceHandle: 'right', targetSide: 'left', type: 'straight' },
  'form-groupers': { target: 'form-proposals', sourceHandle: 'right', targetSide: 'left', type: 'straight' },
  'classify-workers': { target: 'classify-validation', sourceHandle: 'bottom', targetSide: 'top', type: 'straight' },
  'decide-researchers': { target: 'decide-target-base', sourceHandle: 'right', targetSide: 'left', type: 'straight' },
} as const satisfies Readonly<Record<string, {
  target: string;
  sourceHandle: DispatcherPortId;
  targetSide: 'top' | 'left';
  type: 'bezier' | 'straight';
}>>;
const AGENT_PORT_ORDINALS = Array.from({ length: VISIBLE_INVOCATION_LIMIT }, (_, index) => index + 1) as DispatcherPortOrdinal[];

const portsByNode = new Map<string, Map<DispatcherPortId, DispatcherGraphPort['type']>>();
for (const [source, target, sourceHandle, targetHandle] of DISPATCHER_GRAPH_LINKS) {
  const sourcePorts = portsByNode.get(source) ?? new Map<DispatcherPortId, DispatcherGraphPort['type']>();
  sourcePorts.set(sourceHandle, 'source');
  portsByNode.set(source, sourcePorts);
  const targetPorts = portsByNode.get(target) ?? new Map<DispatcherPortId, DispatcherGraphPort['type']>();
  targetPorts.set(targetHandle, 'target');
  portsByNode.set(target, targetPorts);
}

const stageNode = (stage: StageDefinition, state = 'unknown'): Node<DispatcherGraphNodeData> => ({
  id: stage.id,
  type: 'stage',
  position: { x: stage.x, y: 0 },
  style: { width: stage.width, height: STAGE_HEIGHT, background: stage.background, border: `2px solid ${stage.accent}44`, borderRadius: 14 },
  data: { kind: 'stage', circuitId: stage.id, interaction: 'open-circuit', label: stage.label, accent: stage.accent, state },
  draggable: false,
  connectable: false,
  selectable: true,
  ariaRole: 'button',
  ariaLabel: `${stage.label}: ${state}`,
});

const zoneNode = (
  zone: ZoneDefinition,
  data: Partial<DispatcherGraphNodeData> = {},
): Node<DispatcherGraphNodeData> => ({
  id: zone.id,
  parentId: zone.circuitId,
  extent: 'parent',
  type: 'zone',
  position: { x: zone.x, y: zone.y },
  style: { width: zone.width, height: zone.height },
  data: {
    kind: 'zone',
    circuitId: zone.circuitId,
    interaction: 'open-circuit',
    label: zone.label,
    accent: zone.accent,
    ports: [...(portsByNode.get(zone.id) ?? [])].map(([id, type]) => ({ id, type })),
    state: 'unknown',
    ...data,
  },
  draggable: false,
  connectable: false,
  selectable: true,
  ariaRole: 'button',
  ariaLabel: `${zone.label}: ${data.state ?? 'unknown'}`,
});

const structuralEdge = (
  source: string,
  target: string,
  sourceHandle: DispatcherPortId,
  targetHandle: DispatcherPortId,
  active = false,
  type: 'smoothstep' | 'bezier' | 'straight' = 'smoothstep',
  accentOverride?: string,
): Edge => {
  const sourceZone = DISPATCHER_GRAPH_ZONES.find((zone) => zone.id === source);
  const accent = accentOverride ?? sourceZone?.accent ?? '#607d8b';
  return {
    id: `flow:${source}:${target}`,
    source,
    target,
    sourceHandle,
    targetHandle,
    type,
    animated: active,
    selectable: false,
    focusable: false,
    interactionWidth: 0,
    style: { stroke: accent, strokeWidth: 2 },
    markerEnd: { type: MarkerType.ArrowClosed, color: accent, width: 12, height: 12 },
  };
};

const circuitState = (projection: DispatcherProjection | undefined, id: CircuitId) =>
  projection?.circuits.find((circuit) => circuit.id === id)?.state ?? 'unknown';

const zoneState = (projection: DispatcherProjection | undefined, zone: ZoneDefinition) =>
  projection?.circuits.find((circuit) => circuit.id === zone.circuitId)?.zones?.find((item) => item.id === zone.id)?.state;

const boundedCollection = <T extends { id: string }>(items: readonly T[], limit: number, total?: number) => {
  const shown = visibleWindow(items, limit);
  return {
    items: shown.map((item) => item.id),
    lines: [typeof total === 'number' ? `Показано ${shown.length} из ${total}` : `Показано ${shown.length} из доступного окна`],
  };
};

const roleWindowValid = (role: {
  invocations: readonly AgentInvocationProjection[];
  invocation_total?: number;
  invocation_omitted?: number;
}) => {
  const fieldsPresent = role.invocation_total !== undefined || role.invocation_omitted !== undefined;
  return !fieldsPresent || (
    typeof role.invocation_total === 'number'
    && typeof role.invocation_omitted === 'number'
    && role.invocation_total >= role.invocations.length
    && role.invocation_omitted === role.invocation_total - role.invocations.length
  );
};

const agentZoneContent = (
  projection: DispatcherProjection,
  zone: ZoneDefinition,
): Pick<DispatcherGraphNodeData, 'lines' | 'state' | 'active'> => {
  if (!zone.agentRole) return {};
  const phase = phaseForCircuit(projection, zone.circuitId);
  const roles = phase?.roles.filter((role) => role.role_id === zone.agentRole) ?? [];
  if (!roles.length) return { state: 'waiting', lines: ['Фактических вызовов нет'] };
  const unavailable = roles.find((role) => role.environment_status && role.environment_status !== 'ready');
  const valid = roles.every((role) => roleProgress(role).valid && roleWindowValid(role));
  const running = roles.reduce((total, role) => total + role.running, 0);
  const configured = roles.reduce((total, role) => total + role.configured_slots, 0);
  const requested = roles.reduce((total, role) => total + role.requested, 0);
  const omitted = roles.reduce((total, role) =>
    total
    + (role.invocation_omitted ?? 0)
    + Math.max(role.invocations.length - VISIBLE_INVOCATION_LIMIT, 0), 0);
  return {
    state: valid && !unavailable ? running ? 'active' : 'waiting' : 'error',
    active: valid && !unavailable && running > 0,
    lines: [
      `Назначено: ${requested} · настроено: ${configured}`,
      ...roles.flatMap((role) => role.model ? [`${role.model} · ${role.environment_status || 'доступность не подтверждена'}`] : []),
      ...(valid && omitted ? [`Не показано вызовов: ${omitted}`] : []),
      ...(unavailable ? [`Исполнитель недоступен: ${unavailable.environment_status}`] : []),
      ...(!valid ? ['Противоречивая проекция роли'] : []),
    ],
  };
};

const zoneContent = (projection: DispatcherProjection, id: string): Pick<DispatcherGraphNodeData, 'items' | 'lines' | 'subzones' | 'state' | 'active'> => {
  const { items } = projection;
  const aggregates = (circuitId: CircuitId) =>
    projection.circuits.find((circuit) => circuit.id === circuitId)?.aggregates ?? {};
  switch (id) {
    case 'sources': {
      const prepare = projection.circuits.find((circuit) => circuit.id === 'prepare-diffs');
      const sourceZones = [
        ['vendor-baseline', 'Типовая база'],
        ['target-cf', 'Рабочая база'],
        ['next-vendor', 'Новая типовая'],
      ] as const;
      return {
        state: prepare?.state ?? 'unknown',
        subzones: sourceZones.map(([zoneId, label]) => ({
          id: zoneId,
          label,
          state: prepare?.zones?.find((zone) => zone.id === zoneId)?.state ?? 'unknown',
        })),
      };
    }
    case 'prepare-dif-window':
    case 'analyze-dif-window': {
      const total = id === 'analyze-dif-window' && typeof aggregates('analyze-dif').window_size === 'number'
        ? aggregates('analyze-dif').window_size as number
        : undefined;
      const collection = boundedCollection(items.dif_queue, VISIBLE_QUEUE_LIMIT, total);
      return { ...collection, state: items.dif_queue.length ? 'active' : 'waiting', active: items.dif_queue.length > 0 };
    }
    case 'analyze-meaning':
    case 'form-meaning':
      return { ...boundedCollection(items.meaning_diffs, VISIBLE_QUEUE_LIMIT), state: items.meaning_diffs.length ? 'complete' : 'waiting' };
    case 'analyze-noise':
      return { ...boundedCollection(items.noise_diffs, VISIBLE_QUEUE_LIMIT), state: items.noise_diffs.length ? 'complete' : 'waiting' };
    case 'form-proposals':
      return { ...boundedCollection(items.proposals, VISIBLE_QUEUE_LIMIT), state: items.proposals.length ? 'active' : 'waiting', active: items.proposals.length > 0 };
    case 'classify-input': {
      const shown = visibleWindow(items.mrqs, VISIBLE_QUEUE_LIMIT);
      const total = aggregates('classify-mrq').input_mrq_count;
      return {
        items: shown.map((item) => `${item.id} · DIF: ${item.dif_ids.length} · доказательств: ${item.evidence_count}`),
        lines: [typeof total === 'number' ? `Показано ${shown.length} из ${total}` : `Показано ${shown.length} из доступного окна`],
        state: items.mrqs.length ? 'complete' : 'waiting',
      };
    }
    case 'classify-batches': {
      const total = aggregates('classify-mrq').batch_count;
      return { ...boundedCollection(items.batches, VISIBLE_QUEUE_LIMIT, typeof total === 'number' ? total : undefined), state: items.batches.length ? 'complete' : 'waiting' };
    }
    case 'classify-validation': {
      const state = String(aggregates('classify-mrq').validation_state || 'missing_or_invalid');
      return { lines: [state === 'valid' ? 'Полное непересекающееся покрытие подтверждено' : 'Поколение пакетов отсутствует или недействительно'], state: state === 'valid' ? 'complete' : 'waiting' };
    }
    case 'decide-mrq-queue': {
      const decided = new Set(items.decisions.map((item) => item.id));
      const pending = items.mrqs.filter((item) => !decided.has(item.id));
      return { ...boundedCollection(pending, VISIBLE_QUEUE_LIMIT), state: pending.length ? 'active' : 'waiting', active: pending.length > 0 };
    }
    case 'decide-approval':
      return { lines: [`Ожидают: ${items.approval_count}`], state: items.approval_count ? 'waiting' : 'unknown' };
    case 'decide-outcomes': {
      const shown = visibleWindow(items.decisions, VISIBLE_DECISION_LIMIT);
      const labels: Record<string, string> = {
        adopt_vendor: 'Принять типовое',
        adapt: 'Адаптировать',
        retain_custom: 'Сохранить доработку',
        out_of_scope: 'Вне объёма',
      };
      const total = aggregates('decide-target').decision_count;
      return {
        items: shown.map((item) => `${item.id} · ${labels[item.decision] ?? item.decision}`),
        lines: [typeof total === 'number' ? `Показано ${shown.length} из ${total}` : `Показано ${shown.length} из доступного окна`],
        state: items.decisions.length ? 'complete' : 'waiting',
      };
    }
    case 'decide-summary':
      return {
        lines: [
          `Решений в окне: ${items.decisions.length}`,
          `Типовой механизм: ${items.decisions.filter((item) => item.decision === 'adopt_vendor').length} · Адаптировать: ${items.decisions.filter((item) => item.decision === 'adapt').length}`,
          `Сохранить доработку: ${items.decisions.filter((item) => item.decision === 'retain_custom').length} · Вне объёма: ${items.decisions.filter((item) => item.decision === 'out_of_scope').length}`,
        ],
        state: items.decisions.length ? 'complete' : 'waiting',
      };
    default:
      return {};
  }
};

const phaseForCircuit = (projection: DispatcherProjection, circuitId: CircuitId) =>
  projection.agent_phases?.find((phase) => phase.phase_id === circuitId || (circuitId === 'classify-mrq' && phase.phase_id === 'classify-batches') || (circuitId === 'decide-target' && phase.phase_id === 'research-target'));

const invocationState = (status: string) => ({
  running: 'Выполняется',
  queued: 'Ожидает',
  completed: 'Завершено',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
}[status] ?? status);

function agentNodes(projection: DispatcherProjection): Node<DispatcherGraphNodeData>[] {
  const nodes: Node<DispatcherGraphNodeData>[] = [];
  const seenInvocations = new Set<string>();
  for (const zone of DISPATCHER_GRAPH_ZONES.filter((item) => item.agentRole)) {
    const phase = phaseForCircuit(projection, zone.circuitId);
    const roles = phase?.roles.filter((role) => role.role_id === zone.agentRole) ?? [];
    const slots: Array<{ roleId: string; slotId: string; invocations: AgentInvocationProjection[] }> = [];
    for (const role of roles) {
      const roleSlots = new Map<string, AgentInvocationProjection[]>();
      for (const invocation of visibleWindow(role.invocations, VISIBLE_INVOCATION_LIMIT)) {
        if (seenInvocations.has(invocation.invocation_id)) continue;
        seenInvocations.add(invocation.invocation_id);
        const values = roleSlots.get(invocation.slot_id) ?? [];
        values.push(invocation);
        roleSlots.set(invocation.slot_id, values);
      }
      roleSlots.forEach((invocations, slotId) => slots.push({ roleId: role.role_id, slotId, invocations }));
    }
    const dense = slots.length > 4;
    const maxRows = Math.max(1, Math.floor((zone.height - 38) / 48));
    const columns = dense ? Math.min(4, Math.ceil(slots.length / maxRows)) : 1;
    const gap = 6;
    const width = (zone.width - 20 - gap * (columns - 1)) / columns;
    slots.forEach(({ roleId, slotId, invocations }, index) => {
        const active = invocations.some((item) => item.status === 'running');
        const failed = invocations.some((item) => item.status === 'failed');
        const id = `agent:${phase?.phase_id ?? zone.circuitId}:${roleId}:${slotId}`;
        const height = dense ? 40 : Math.max(40, 28 + invocations.length * 12);
        const column = index % columns;
        const row = Math.floor(index / columns);
        nodes.push({
          id,
          parentId: zone.id,
          extent: 'parent',
          type: 'agent',
          position: { x: 10 + column * (width + gap), y: 38 + row * 48 },
          style: { width: Math.max(width, 28), height },
          data: {
            kind: 'agent',
            circuitId: zone.circuitId,
            interaction: 'open-circuit',
            label: slotId,
            accent: failed ? '#d32f2f' : zone.accent,
            state: failed ? 'Ошибка' : active ? 'Выполняется' : invocationState(invocations.at(-1)?.status ?? 'unknown'),
            items: invocations.map((item) => item.work_unit_id),
            lines: invocations.map((item) => item.invocation_id),
            invocations: invocations.map((item) => ({
              id: item.invocation_id,
              workUnitId: item.work_unit_id,
              state: invocationState(item.status),
              updatedAt: item.updated_at,
            })),
            ports: active
              ? [{ id: DISPATCHER_AGENT_FLOWS[zone.id as keyof typeof DISPATCHER_AGENT_FLOWS]?.sourceHandle ?? 'right', type: 'source' }]
              : [],
            active,
          },
          draggable: false,
          connectable: false,
          selectable: true,
          ariaRole: 'button',
          ariaLabel: `Слот ${slotId}: ${invocations.length} вызовов`,
        });
    });
    for (const role of roles) {
      // Keep the consistency check in the model without inventing empty slot nodes.
      if (!roleProgress(role).valid) {
        const parent = [...nodes].reverse().find((node) => node.parentId === zone.id);
        if (parent) parent.data.state = 'Противоречивая проекция';
      }
    }
  }
  return nodes;
}

export function buildDispatcherGraph(projection?: DispatcherProjection): DispatcherGraphModel {
  const stages = DISPATCHER_GRAPH_STAGES.map((stage) => stageNode(stage, circuitState(projection, stage.id)));
  const zones = DISPATCHER_GRAPH_ZONES.map((zone) => {
    const content = projection
      ? { ...zoneContent(projection, zone.id), ...agentZoneContent(projection, zone) }
      : {};
    return zoneNode(zone, {
      ...content,
      state: projection
        ? content.state === 'error' ? 'error' : zoneState(projection, zone) ?? content.state ?? 'unknown'
        : 'unknown',
    });
  });
  const agents = projection ? agentNodes(projection) : [];
  const dynamicEdges = Object.entries(DISPATCHER_AGENT_FLOWS).flatMap(([agentZone, flow]) =>
    agents.filter((node) => node.parentId === agentZone).flatMap((node, index) => {
      if (!node.data.active) return [];
      const targetPort = `${flow.targetSide}-${AGENT_PORT_ORDINALS[(index * 5 + 3) % AGENT_PORT_ORDINALS.length]}` as DispatcherPortId;
      const target = zones.find((zone) => zone.id === flow.target);
      target?.data.ports?.push({ id: targetPort, type: 'target' });
      return [{
        ...structuralEdge(node.id, flow.target, flow.sourceHandle, targetPort, true, flow.type, node.data.accent),
        id: `agent-flow:${node.id}:${flow.target}`,
      }];
    }));
  return {
    // React Flow requires every parent before its children.
    nodes: [...stages, ...zones, ...agents],
    edges: [
      ...DISPATCHER_GRAPH_LINKS.map(([source, target, sourceHandle, targetHandle]) =>
        structuralEdge(source, target, sourceHandle, targetHandle)),
      ...dynamicEdges,
    ],
  };
}

export const STATIC_COMPLETE_DISPATCHER_PROJECTION: DispatcherProjection = {
  schema_version: '2',
  revision: 1,
  fresh_at: '2026-01-01T09:00:00Z',
  circuits: DISPATCHER_GRAPH_STAGES.map((stage) => ({
    id: stage.id,
    state: stage.id === 'prepare-diffs' ? 'complete' : 'ready',
    zones: DISPATCHER_GRAPH_ZONES.filter((zone) => zone.circuitId === stage.id).map((zone) => ({
      id: zone.id,
      state: zone.id.includes('workers') || zone.id.includes('groupers') || zone.id.includes('researchers') ? 'active' : 'complete',
    })),
  })),
  jobs: {},
  items: {
    dif_queue: ['DIF-001', 'DIF-002', 'DIF-003', 'DIF-004'].map((id) => ({ id, path: `fixture/${id}`, kind: 'metadata', state: 'ready' })),
    meaning_diffs: ['DIF-002', 'DIF-003'].map((id) => ({ id, path: `fixture/${id}`, kind: 'semantic', state: 'ready' })),
    noise_diffs: [{ id: 'DIF-001', path: 'fixture/DIF-001', kind: 'noise', state: 'ready' }],
    proposals: [{ id: 'proposal-1', job_id: 'discover-mrq', kind: 'group', semantic_key: 'order', dif_ids: ['DIF-002'], evidence_count: 2, noise_count: 0, mrq_id: 'MRQ-014', created_at: '2026-01-01T09:00:00Z' }],
    mrqs: [{ id: 'MRQ-014', title: 'Заказ', semantic_key: 'order', state: 'ready', dif_ids: ['DIF-002'], evidence_count: 2 }],
    batches: [{ id: 'MRQB-001', mrq_ids: ['MRQ-014'], reason: 'Исследование цели' }],
    decisions: [{ id: 'MRQ-014', title: 'Заказ', decision: 'adapt', target_solution: 'Подтверждённое решение', evidence_count: 3, gap: true }],
    approval_count: 1,
  },
  agent_phases: [
    {
      job_id: 'discover-mrq', phase_id: 'analyze-dif', mode: 'bounded-parallel', max_concurrency: 4,
      roles: [{
        role_id: 'analyzer', agent_profile: 'local', configured_slots: 4, requested: 4,
        running: 2, queued: 0, completed: 2, failed: 0, cancelled: 0, interrupted: 0,
        invocations: [
          { invocation_id: 'analyze-1', slot_id: 'analyzer-1', work_unit_id: 'DIF-001', status: 'completed' },
          { invocation_id: 'analyze-2', slot_id: 'analyzer-2', work_unit_id: 'DIF-002', status: 'running' },
          { invocation_id: 'analyze-3', slot_id: 'analyzer-3', work_unit_id: 'DIF-003', status: 'completed' },
          { invocation_id: 'analyze-4', slot_id: 'analyzer-4', work_unit_id: 'DIF-004', status: 'running' },
        ],
      }],
    },
    {
      job_id: 'discover-mrq', phase_id: 'form-mrq', mode: 'coordinated-pool', max_concurrency: 4,
      roles: [
        { role_id: 'coordinator', agent_profile: 'local', configured_slots: 1, requested: 1, running: 1, queued: 0, completed: 0, failed: 0, cancelled: 0, interrupted: 0, invocations: [{ invocation_id: 'coordinate-1', slot_id: 'coordinator-1', work_unit_id: 'DIF-002', status: 'running' }] },
        { role_id: 'grouper', agent_profile: 'local', configured_slots: 4, requested: 4, running: 2, queued: 2, completed: 0, failed: 0, cancelled: 0, interrupted: 0, invocations: [{ invocation_id: 'group-1', slot_id: 'grouper-1', work_unit_id: 'DIF-001', status: 'running' }, { invocation_id: 'group-2', slot_id: 'grouper-2', work_unit_id: 'DIF-002', status: 'running' }, { invocation_id: 'group-3', slot_id: 'grouper-3', work_unit_id: 'DIF-003', status: 'queued' }, { invocation_id: 'group-4', slot_id: 'grouper-4', work_unit_id: 'DIF-004', status: 'queued' }] },
      ],
    },
    {
      job_id: 'classify-mrq', phase_id: 'classify-batches', mode: 'sequential', max_concurrency: 1,
      roles: [{
        role_id: 'classifier', agent_profile: 'local', configured_slots: 1, requested: 1,
        running: 1, queued: 0, completed: 0, failed: 0, cancelled: 0, interrupted: 0,
        invocations: [{ invocation_id: 'classify-1', slot_id: 'classifier-1', work_unit_id: 'window:0', status: 'running' }],
      }],
    },
    {
      job_id: 'decide-mrq', phase_id: 'research-target', mode: 'bounded-parallel', max_concurrency: 4,
      roles: [{
        role_id: 'researcher', agent_profile: 'local', configured_slots: 4, requested: 4,
        running: 2, queued: 2, completed: 0, failed: 0, cancelled: 0, interrupted: 0,
        invocations: [{ invocation_id: 'research-1', slot_id: 'researcher-1', work_unit_id: 'MRQ-014', status: 'running' }, { invocation_id: 'research-2', slot_id: 'researcher-2', work_unit_id: 'MRQ-015', status: 'queued' }, { invocation_id: 'research-3', slot_id: 'researcher-3', work_unit_id: 'MRQ-016', status: 'running' }, { invocation_id: 'research-4', slot_id: 'researcher-4', work_unit_id: 'MRQ-017', status: 'queued' }],
      }],
    },
  ],
};

export const STATIC_COMPLETE_DISPATCHER_GRAPH = buildDispatcherGraph(STATIC_COMPLETE_DISPATCHER_PROJECTION);
