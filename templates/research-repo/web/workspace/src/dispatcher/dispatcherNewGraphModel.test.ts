import { describe, expect, test } from 'vitest';
import { activeProjection, emptyProjection, errorProjection, saturatedProjection } from '../../e2e/dispatcher.fixtures';
import { ENRICHED_SUBFLOW_EDGES, ENRICHED_SUBFLOW_NODES } from './EnrichedSubFlowReference';
import { buildDispatcherNewGraph } from './dispatcherNewGraphModel';

const nodeGeometry = (node: (typeof ENRICHED_SUBFLOW_NODES)[number]) => ({
  id: node.id,
  parentId: node.parentId,
  position: node.position,
  style: node.style,
});

const edgeGeometry = (edge: (typeof ENRICHED_SUBFLOW_EDGES)[number]) => ({
  id: edge.id,
  source: edge.source,
  target: edge.target,
  sourceHandle: edge.sourceHandle,
  targetHandle: edge.targetHandle,
  style: edge.style,
  markerEnd: edge.markerEnd,
});

const redundantAgentNodes = new Set([
  'analyzer-2', 'analyzer-3',
  'grouper-2', 'grouper-3',
  'researcher-2', 'researcher-3',
]);
const roleNodes = new Set(['analyzer-1', 'coordinator', 'grouper-1', 'classifier-1', 'researcher-1']);
const roleHandle = (handle: string | null | undefined, direction: 'in' | 'out') =>
  `${direction}-${handle?.endsWith('top') ? 'top' : handle?.endsWith('bottom') ? 'bottom' : 'mid'}`;

describe('dispatcherNewGraphModel', () => {
  const nestedZones = (projection: typeof saturatedProjection) =>
    new Map(buildDispatcherNewGraph(projection).nodes
      .flatMap((node) => node.data.subzones ?? [])
      .map((zone) => [zone.id, zone]));

  test('меняет только данные узлов, сохраняя буквальную геометрию и линии эталона', () => {
    const graph = buildDispatcherNewGraph(saturatedProjection);
    const nodes = ENRICHED_SUBFLOW_NODES.filter((node) => !redundantAgentNodes.has(node.id));
    const nodeIds = new Set(nodes.map((node) => node.id));
    expect(graph.nodes.map(nodeGeometry)).toEqual(nodes.map(nodeGeometry));
    expect(graph.edges.map(edgeGeometry)).toEqual(ENRICHED_SUBFLOW_EDGES
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => edgeGeometry({
        ...edge,
        sourceHandle: roleNodes.has(edge.source) ? roleHandle(edge.sourceHandle, 'out') : edge.sourceHandle,
        targetHandle: roleNodes.has(edge.target) ? roleHandle(edge.targetHandle, 'in') : edge.targetHandle,
      })));
    expect(graph.nodes.every((node) =>
      node.focusable
      && node.ariaRole === 'button'
      && node.data.interaction === 'open-circuit'
      && Boolean(node.data.circuitId)
      && Boolean(node.ariaLabel))).toBe(true);
  });

  test('показывает фактические значения проекции вместо демонстрационных', () => {
    const graph = buildDispatcherNewGraph(saturatedProjection);
    const byId = new Map(graph.nodes.map((node) => [node.id, node.data]));

    expect(byId.get('analyzer-1')).toMatchObject({ zoneId: 'analyze-workers', state: 'В работе', active: true });
    expect(byId.get('coordinator')).toMatchObject({ zoneId: 'form-coordinator', state: 'Готово', active: false });
    expect(byId.get('grouper-1')).toMatchObject({ zoneId: 'form-groupers', state: 'В работе', active: true });
    expect(byId.get('researcher-1')).toMatchObject({ zoneId: 'decide-researchers', state: 'В работе', active: true });
    expect(byId.get('semantic-dif')?.detail).toContain('DIF-00021');
    expect(byId.get('publication')).toMatchObject({ zoneId: 'form-publication', detail: expect.stringContaining('MRQ-00001 · DIF 1 · доказательств 2') });
    expect(byId.get('results')?.detail).toContain('типовых: 1');
    expect(graph.nodes.flatMap((node) => node.data.invocations ?? [])).toHaveLength(24);
    expect(new Set(graph.nodes.flatMap((node) => node.data.invocations?.map((item) => item.id) ?? [])).size).toBe(24);
    expect(graph.nodes.filter((node) => node.type === 'role')).toHaveLength(5);
    expect(graph.nodes.some((node) => redundantAgentNodes.has(node.id))).toBe(false);
    expect([...nestedZones(saturatedProjection).keys()]).toEqual([
      'analyze-noise',
      'form-barrier',
      'form-publication',
      'form-summary',
      'decide-approval',
      'decide-outcomes',
    ]);
    expect(byId.get('batch-output')).toMatchObject({ state: 'Готово', detail: expect.stringContaining('MRQB-') });
    expect(nestedZones(saturatedProjection).get('decide-approval')).toMatchObject({ state: 'Ожидает', detail: 'Ожидают: 1' });

    const serialized = JSON.stringify(graph);
    for (const demo of ['77 079', '1 342', '3.0.4', '28 731', 'прогресс 45%', 'прогресс 65%']) {
      expect(serialized).not.toContain(demo);
    }
  });

  test('оставляет те же карточки при пустой проекции и сообщает об отсутствии данных', () => {
    const graph = buildDispatcherNewGraph(emptyProjection);
    expect(graph.nodes.map((node) => node.id)).toEqual(ENRICHED_SUBFLOW_NODES
      .filter((node) => !redundantAgentNodes.has(node.id))
      .map((node) => node.id));
    expect(graph.nodes.find((node) => node.id === 'analyzer-1')?.data).toMatchObject({
      zoneId: 'analyze-workers',
      state: 'Ожидает',
      detail: 'Работа не назначена',
      invocations: [],
    });
    expect(graph.nodes.find((node) => node.id === 'target-db')?.data.detail).toBe('Версия и размер не подтверждены проекцией');
    expect([...nestedZones(emptyProjection).values()].every((zone) =>
      zone.state === 'Ожидает' || zone.state === 'Недоступно')).toBe(true);
  });

  test('отражает активное и ошибочное состояния из проекции', () => {
    const active = buildDispatcherNewGraph(activeProjection);
    expect(active.nodes.find((node) => node.data.zoneId === 'analyze-workers')?.data.active).toBe(true);
    expect(active.edges.some((edge) => edge.animated)).toBe(true);

    const failed = buildDispatcherNewGraph(errorProjection);
    expect(failed.nodes.find((node) => node.data.zoneId === 'diffs-build')?.data).toMatchObject({
      state: 'Ошибка',
      active: false,
    });
    expect(nestedZones(errorProjection).get('form-publication')?.state).toBe('Ожидает');
  });
});
