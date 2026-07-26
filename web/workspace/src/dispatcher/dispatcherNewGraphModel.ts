import type { Edge, Node } from '@xyflow/react';
import { ENRICHED_SUBFLOW_EDGES, ENRICHED_SUBFLOW_NODES } from './EnrichedSubFlowReference';
import type { EnrichedDispatcherModel, EnrichedNodeData } from './EnrichedDispatcherGraph';
import {
  VISIBLE_QUEUE_LIMIT,
  visibleWindow,
  type AgentInvocationProjection,
  type CircuitId,
  type DispatcherProjection,
  type DispatcherZoneProjection,
} from './projection';

const CIRCUIT_BY_STAGE: Record<string, CircuitId> = {
  prepare: 'prepare-diffs',
  analysis: 'analyze-dif',
  mrq: 'form-mrq',
  classify: 'classify-mrq',
  target: 'decide-target',
};

const ZONE_BY_NODE: Record<string, string> = {
  sources: 'sources',
  acquire: 'sources-acquire',
  index: 'indexes-build',
  diff: 'diffs-build',
  'dif-queue': 'prepare-dif-window',
  'analysis-queue': 'analyze-dif-window',
  'analyzer-1': 'analyze-workers',
  'semantic-dif': 'analyze-meaning',
  'semantic-queue': 'form-meaning',
  coordinator: 'form-coordinator',
  'grouper-1': 'form-groupers',
  proposal: 'form-proposals',
  review: 'form-review',
  publication: 'form-publication',
  'batch-input': 'classify-input',
  'classifier-1': 'classify-workers',
  'batch-validation': 'classify-validation',
  'batch-output': 'classify-batches',
  'mrq-queue': 'decide-mrq-queue',
  'researcher-1': 'decide-researchers',
  'target-db': 'decide-target-base',
  results: 'decide-summary',
};

const ROLE_BY_NODE: Record<string, string> = {
  'analyzer-1': 'analyzer',
  coordinator: 'coordinator',
  'grouper-1': 'grouper',
  'classifier-1': 'classifier',
  'researcher-1': 'researcher',
};

const ROLE_TITLE: Record<string, string> = {
  analyzer: 'Фактические анализаторы',
  coordinator: 'Координатор',
  grouper: 'Фактические группировщики',
  classifier: 'Фактический классификатор',
  researcher: 'Фактические исследователи',
};

const REDUNDANT_AGENT_NODES = new Set([
  'analyzer-2',
  'analyzer-3',
  'grouper-2',
  'grouper-3',
  'researcher-2',
  'researcher-3',
]);

const ROLE_PORTS: EnrichedNodeData['ports'] = [
  { id: 'in-top', type: 'target' },
  { id: 'in-mid', type: 'target' },
  { id: 'in-bottom', type: 'target' },
  { id: 'out-top', type: 'source' },
  { id: 'out-mid', type: 'source' },
  { id: 'out-bottom', type: 'source' },
];

const STATUS_LABEL: Record<string, string> = {
  unknown: 'Недоступно',
  waiting: 'Ожидает',
  active: 'В работе',
  running: 'Выполняется',
  queued: 'Ожидает',
  error: 'Ошибка',
  failed: 'Ошибка',
  complete: 'Готово',
  completed: 'Завершено',
  ready: 'Готово',
  blocked: 'Ожидает',
  cancelled: 'Отменено',
  interrupted: 'Прервано',
};

const statusLabel = (status?: string) => STATUS_LABEL[status ?? 'unknown'] ?? status ?? 'Недоступно';

const circuitState = (projection: DispatcherProjection, id: CircuitId) =>
  statusLabel(projection.circuits.find((circuit) => circuit.id === id)?.state);

const zone = (projection: DispatcherProjection, id: string): DispatcherZoneProjection | undefined => {
  for (const circuit of projection.circuits) {
    const current = circuit.zones?.find((candidate) => candidate.id === id);
    if (current) return current;
    if (id === 'publication' && circuit.publication) return circuit.publication;
  }
  return undefined;
};

const zonePatch = (projection: DispatcherProjection, id: string): Partial<EnrichedNodeData> => {
  const current = zone(projection, id);
  if (!current) return { state: 'Недоступно', detail: 'Состояние не передано сервером', active: false };
  return {
    state: statusLabel(current.state),
    detail: [
      current.count === undefined ? '' : `Количество: ${current.count}`,
      current.updated_at ? `Подтверждено ${new Date(current.updated_at).toLocaleTimeString('ru-RU')}` : '',
    ].filter(Boolean).join(' · ') || 'Состояние подтверждено',
    active: current.state === 'active',
  };
};

const listPatch = (
  values: readonly { id: string }[],
  empty = 'Данных пока нет',
): Partial<EnrichedNodeData> => {
  const shown = visibleWindow(values, VISIBLE_QUEUE_LIMIT);
  return {
    state: values.length ? 'Готово' : 'Ожидает',
    detail: shown.length ? shown.map((item) => item.id).join(' · ') : empty,
    active: false,
  };
};

const collectionDetail = (values: readonly { id: string }[], empty: string) =>
  visibleWindow(values, VISIBLE_QUEUE_LIMIT).map((item) => item.id).join(' · ') || empty;

const nestedZone = (
  id: string,
  title: string,
  values: readonly { id: string }[],
  empty: string,
): NonNullable<EnrichedNodeData['subzones']>[number] => ({
  id,
  title,
  state: values.length ? 'Готово' : 'Ожидает',
  detail: collectionDetail(values, empty),
});

const roleInvocations = (projection: DispatcherProjection, roleId: string) =>
  projection.agent_phases
    ?.flatMap((phase) => phase.roles.filter((role) => role.role_id === roleId))
    .flatMap((role) => role.invocations) ?? [];

const rolePatch = (
  projection: DispatcherProjection,
  roleId: string,
): Partial<EnrichedNodeData> => {
  const invocations = roleInvocations(projection, roleId);
  return {
    title: ROLE_TITLE[roleId],
    state: invocations.some((item) => item.status === 'running') ? 'В работе' : invocations.length ? 'Готово' : 'Ожидает',
    detail: invocations.length ? `Вызовов: ${invocations.length}` : 'Работа не назначена',
    active: invocations.some((item) => item.status === 'running'),
    invocations: invocations.map((item) => ({
      id: item.invocation_id,
      slotId: item.slot_id,
      workUnitId: item.work_unit_id,
      state: statusLabel(item.status),
      updatedAt: item.updated_at,
    })),
    ports: ROLE_PORTS,
  };
};

const invocationPatch = (
  invocation: AgentInvocationProjection | undefined,
  emptyTitle: string,
): Partial<EnrichedNodeData> => invocation ? {
  title: invocation.slot_id || emptyTitle,
  state: statusLabel(invocation.status),
  detail: [
    invocation.work_unit_id,
    invocation.updated_at ? new Date(invocation.updated_at).toLocaleTimeString('ru-RU') : '',
  ].filter(Boolean).join(' · '),
  active: invocation.status === 'running',
} : {
  title: emptyTitle,
  state: 'Ожидает',
  detail: 'Работа не назначена',
  active: false,
};

function dataPatch(projection: DispatcherProjection, nodeId: string): Partial<EnrichedNodeData> {
  const role = ROLE_BY_NODE[nodeId];
  if (role) return rolePatch(projection, role);
  const analyzer = roleInvocations(projection, 'analyzer');
  const coordinator = roleInvocations(projection, 'coordinator');
  const grouper = roleInvocations(projection, 'grouper');
  const classifier = roleInvocations(projection, 'classifier');
  const researcher = roleInvocations(projection, 'researcher');
  const stage = CIRCUIT_BY_STAGE[nodeId];
  if (stage) return { state: circuitState(projection, stage), active: false };

  switch (nodeId) {
    case 'sources': {
      const sourceZones = ['vendor-baseline', 'target-cf', 'next-vendor'].map((id) => zone(projection, id)).filter(Boolean);
      const states = sourceZones.map((item) => item!.state);
      return {
        state: states.includes('error') ? 'Ошибка' : states.length && states.every((state) => state === 'complete') ? 'Готово' : 'Недоступно',
        detail: states.length ? `Подтверждено источников: ${states.filter((state) => state === 'complete').length} из ${states.length}` : 'Состояние источников не передано сервером',
        active: states.includes('active'),
      };
    }
    case 'acquire':
      return zonePatch(projection, 'sources-acquire');
    case 'index':
      return zonePatch(projection, 'indexes-build');
    case 'diff':
      return zonePatch(projection, 'diffs-build');
    case 'dif-queue':
    case 'analysis-queue':
      return listPatch(projection.items.dif_queue, 'Очередь пуста');
    case 'analyzer-1':
      return invocationPatch(analyzer[0], 'Анализатор 1');
    case 'analyzer-2':
      return invocationPatch(analyzer[1], 'Анализатор 2');
    case 'analyzer-3':
      return invocationPatch(analyzer[2], 'Анализатор 3');
    case 'semantic-dif':
      return {
        ...listPatch(projection.items.meaning_diffs, 'Смысловых DIF пока нет'),
        detail: [
          projection.items.meaning_diffs.length
            ? `Смысловые: ${visibleWindow(projection.items.meaning_diffs, VISIBLE_QUEUE_LIMIT).map((item) => item.id).join(' · ')}`
            : 'Смысловых DIF пока нет',
          `Технический шум: ${projection.items.noise_diffs.length}`,
        ].join(' · '),
        subzones: [
          nestedZone('analyze-noise', 'Технический шум', projection.items.noise_diffs, 'Технического шума пока нет'),
        ],
      };
    case 'semantic-queue':
      return listPatch(projection.items.meaning_diffs, 'Смысловых DIF пока нет');
    case 'coordinator':
      return invocationPatch(coordinator[0], 'Координатор MRQ');
    case 'grouper-1':
      return invocationPatch(grouper[0], 'Группировщик 1');
    case 'grouper-2':
      return invocationPatch(grouper[1], 'Группировщик 2');
    case 'grouper-3':
      return invocationPatch(grouper[2], 'Группировщик 3');
    case 'proposal': {
      const proposal = projection.items.proposals[0];
      return proposal ? {
        state: 'Предложено',
        detail: `${proposal.id} · ${visibleWindow(proposal.dif_ids.map((id) => ({ id })), VISIBLE_QUEUE_LIMIT).map((item) => item.id).join(' · ')}`,
        active: false,
      } : { state: 'Ожидает', detail: 'Предложений пока нет', active: false };
    }
    case 'review':
      return {
        state: projection.items.proposals.length ? 'В работе' : 'Ожидает',
        detail: projection.items.proposals.length
          ? `Предложений: ${projection.items.proposals.length} · доказательств: ${projection.items.proposals.reduce((sum, item) => sum + item.evidence_count, 0)}`
          : 'Предложений пока нет',
        active: false,
        subzones: [{
          id: 'form-barrier',
          title: 'Барьер полного покрытия',
          state: projection.items.proposals.length ? 'В работе' : 'Ожидает',
          detail: projection.items.proposals.length
            ? `Предложений: ${projection.items.proposals.length} · доказательств: ${projection.items.proposals.reduce((sum, item) => sum + item.evidence_count, 0)}`
            : 'Предложений пока нет',
        }],
      };
    case 'publication':
      return projection.items.mrqs.length ? {
        ...zonePatch(projection, 'publication'),
        detail: visibleWindow(projection.items.mrqs, VISIBLE_QUEUE_LIMIT)
          .map((item) => `${item.id} · DIF ${item.dif_ids.length} · доказательств ${item.evidence_count}`)
          .join(' · '),
        subzones: publicationSubzones(projection),
      } : { ...zonePatch(projection, 'publication'), detail: 'MRQ пока нет', subzones: publicationSubzones(projection) };
    case 'batch-input':
      return listPatch(projection.items.mrqs, 'Исходных MRQ пока нет');
    case 'classifier-1':
      return invocationPatch(classifier[0], 'Классификатор');
    case 'batch-validation': {
      const state = projection.circuits.find((item) => item.id === 'classify-mrq')?.aggregates?.validation_state;
      return { state: state === 'valid' ? 'Готово' : 'Ожидает', detail: state === 'valid' ? 'Полное непересекающееся покрытие подтверждено' : 'Поколение пакетов не подтверждено', active: false };
    }
    case 'batch-output':
      return listPatch(projection.items.batches, 'Пакетов пока нет');
    case 'mrq-queue':
      return listPatch(projection.items.mrqs, 'Очередь MRQ пуста');
    case 'researcher-1':
      return invocationPatch(researcher[0], 'Исследователь 1');
    case 'researcher-2':
      return invocationPatch(researcher[1], 'Исследователь 2');
    case 'researcher-3':
      return invocationPatch(researcher[2], 'Исследователь 3');
    case 'target-db':
      return {
        state: 'Недоступно',
        detail: 'Версия и размер не подтверждены проекцией',
        active: false,
        subzones: [{
          id: 'decide-approval',
          title: 'Ожидание одобрения',
          state: projection.items.approval_count ? 'Ожидает' : 'Недоступно',
          detail: `Ожидают: ${projection.items.approval_count}`,
        }],
      };
    case 'results': {
      const labels: Record<string, string> = {
        adopt_vendor: 'типовых',
        adapt: 'разрывов',
        retain_custom: 'сохранено',
        out_of_scope: 'вне объёма',
      };
      const counts = Object.entries(labels)
        .map(([decision, label]) => `${label}: ${projection.items.decisions.filter((item) => item.decision === decision).length}`)
        .join(' · ');
      return {
        state: projection.items.decisions.length ? `${projection.items.decisions.length} решений` : 'Ожидает',
        detail: projection.items.decisions.length ? counts : 'Решений пока нет',
        active: false,
        subzones: [{
          id: 'decide-outcomes',
          title: 'Исходы исследования',
          state: projection.items.decisions.length ? 'Готово' : 'Ожидает',
          detail: collectionDetail(projection.items.decisions, 'Решений пока нет'),
        }],
      };
    }
    default:
      return {};
  }
}

function publicationSubzones(projection: DispatcherProjection): NonNullable<EnrichedNodeData['subzones']> {
  const publication = zone(projection, 'publication');
  return [
    {
      id: 'form-publication',
      title: 'Публикация',
      state: statusLabel(publication?.state),
      detail: publication?.updated_at ? `Подтверждено ${new Date(publication.updated_at).toLocaleTimeString('ru-RU')}` : 'Состояние не передано сервером',
    },
    {
      id: 'form-summary',
      title: 'Сводка опубликованных MRQ',
      state: projection.items.mrqs.length ? 'Готово' : 'Ожидает',
      detail: `MRQ: ${projection.items.mrqs.length}`,
    },
  ];
}

const cloneNode = (node: Node<EnrichedNodeData>, projection: DispatcherProjection): Node<EnrichedNodeData> => {
  const patch = dataPatch(projection, node.id);
  const role = ROLE_BY_NODE[node.id];
  return {
    ...node,
    type: role ? 'role' : node.type,
    position: { ...node.position },
    style: { ...node.style },
    selectable: true,
    focusable: true,
    ariaRole: 'button',
    ariaLabel: `${node.data.title}: ${patch.state ?? 'Недоступно'}`,
    data: {
      ...node.data,
      zoneId: ZONE_BY_NODE[node.id],
      circuitId: CIRCUIT_BY_STAGE[node.id] ?? CIRCUIT_BY_STAGE[node.parentId ?? ''],
      interaction: 'open-circuit',
      ...patch,
    },
  };
};

const roleHandle = (handle: string | null | undefined, direction: 'in' | 'out') => {
  const position = handle?.endsWith('top') ? 'top' : handle?.endsWith('bottom') ? 'bottom' : 'mid';
  return `${direction}-${position}`;
};

const cloneEdge = (edge: Edge, nodes: Node<EnrichedNodeData>[]): Edge => {
  const source = nodes.find((node) => node.id === edge.source);
  const target = nodes.find((node) => node.id === edge.target);
  return {
    ...edge,
    sourceHandle: source?.type === 'role' ? roleHandle(edge.sourceHandle, 'out') : edge.sourceHandle,
    targetHandle: target?.type === 'role' ? roleHandle(edge.targetHandle, 'in') : edge.targetHandle,
    style: { ...edge.style },
    markerEnd: typeof edge.markerEnd === 'object' ? { ...edge.markerEnd } : edge.markerEnd,
    animated: Boolean(source?.data.active),
  };
};

export function buildDispatcherNewGraph(projection: DispatcherProjection): EnrichedDispatcherModel {
  const nodes = ENRICHED_SUBFLOW_NODES
    .filter((node) => !REDUNDANT_AGENT_NODES.has(node.id))
    .map((node) => cloneNode(node, projection));
  const nodeIds = new Set(nodes.map((node) => node.id));
  return {
    nodes,
    edges: ENRICHED_SUBFLOW_EDGES
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => cloneEdge(edge, nodes)),
  };
}
