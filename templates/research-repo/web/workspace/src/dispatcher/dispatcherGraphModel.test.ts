import { describe, expect, test } from 'vitest';
import {
  DISPATCHER_GRAPH_STAGES,
  DISPATCHER_GRAPH_LINKS,
  DISPATCHER_GRAPH_ZONES,
  STATIC_COMPLETE_DISPATCHER_GRAPH,
  STATIC_COMPLETE_DISPATCHER_PROJECTION,
  buildDispatcherGraph,
} from './dispatcherGraphModel';

describe('dispatcherGraphModel', () => {
  test('содержит полный каркас пяти этапов и обязательных зон', () => {
    expect(DISPATCHER_GRAPH_STAGES).toHaveLength(5);
    expect(new Set(DISPATCHER_GRAPH_ZONES.map((zone) => zone.id))).toEqual(new Set([
      'sources', 'sources-acquire', 'diffs-build', 'indexes-build', 'prepare-dif-window',
      'analyze-dif-window', 'analyze-workers', 'analyze-meaning', 'analyze-noise',
      'form-meaning', 'form-coordinator', 'form-groupers', 'form-proposals', 'form-review',
      'form-barrier', 'form-publication', 'form-summary',
      'classify-input', 'classify-workers', 'classify-validation', 'classify-batches',
      'decide-mrq-queue', 'decide-researchers', 'decide-target-base', 'decide-approval', 'decide-outcomes', 'decide-summary',
    ]));
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.nodes.find((node) => node.id === 'sources')?.data.subzones?.map((zone) => zone.id)).toEqual([
      'vendor-baseline', 'target-cf', 'next-vendor',
    ]);
    const ids = STATIC_COMPLETE_DISPATCHER_GRAPH.nodes.map((node) => node.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.edges.every((edge) => ids.includes(edge.source) && ids.includes(edge.target))).toBe(true);
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.nodes.slice(0, 5).every((node) => node.type === 'stage')).toBe(true);
    for (const stage of DISPATCHER_GRAPH_STAGES) {
      const node = STATIC_COMPLETE_DISPATCHER_GRAPH.nodes.find((item) => item.id === stage.id)!;
      expect(node.type).toBe('stage');
      expect(node.position).toEqual({ x: stage.x, y: 0 });
      expect(node.style).toMatchObject({ width: stage.width, height: 760 });
    }
    for (const zone of DISPATCHER_GRAPH_ZONES) {
      const node = STATIC_COMPLETE_DISPATCHER_GRAPH.nodes.find((item) => item.id === zone.id)!;
      expect(node.type).toBe('zone');
      expect(node.parentId).toBe(zone.circuitId);
      expect(node.position).toEqual({ x: zone.x, y: zone.y });
      expect(node.style).toEqual({ width: zone.width, height: zone.height });
      const expectedPorts = DISPATCHER_GRAPH_LINKS.flatMap(([source, target, sourceHandle, targetHandle]) => [
        ...(source === zone.id ? [`source:${sourceHandle}`] : []),
        ...(target === zone.id ? [`target:${targetHandle}`] : []),
      ]);
      expectedPorts.push(...STATIC_COMPLETE_DISPATCHER_GRAPH.edges.flatMap((edge) =>
        edge.id.startsWith('agent-flow:') && edge.target === zone.id ? [`target:${edge.targetHandle}`] : []));
      expect(node.data.ports?.map((port) => `${port.type}:${port.id}`)).toEqual(expectedPorts);
    }
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.edges.filter((edge) => edge.id.startsWith('flow:')).map((edge) => [
      edge.source,
      edge.target,
      edge.sourceHandle,
      edge.targetHandle,
    ])).toEqual(DISPATCHER_GRAPH_LINKS);
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.edges.filter((edge) => edge.id.startsWith('flow:')).every((edge) =>
      edge.type === 'smoothstep')).toBe(true);
    expect(STATIC_COMPLETE_DISPATCHER_GRAPH.edges.every((edge) =>
      edge.selectable === false && edge.focusable === false)).toBe(true);
  });

  test('строит только фактические слоты со стабильными идентификаторами', () => {
    const projection = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    const analyzer = projection.agent_phases![0].roles[0];
    analyzer.configured_slots = 9;
    analyzer.invocations.push({ ...analyzer.invocations[0], invocation_id: 'analyze-5', work_unit_id: 'DIF-005' });
    const first = buildDispatcherGraph(projection);
    const second = buildDispatcherGraph(projection);
    const agents = first.nodes.filter((node) => node.type === 'agent');
    expect(agents.map((node) => node.id)).toEqual(second.nodes.filter((node) => node.type === 'agent').map((node) => node.id));
    expect(agents.filter((node) => node.id.includes('analyzer'))).toHaveLength(4);
    expect(agents.some((node) => node.id.includes('analyzer-5'))).toBe(false);
    expect(agents.find((node) => node.id.endsWith(':analyzer-1'))?.data.lines).toEqual(['analyze-1', 'analyze-5']);
    expect(first.nodes.filter((node) => node.type === 'agent').flatMap((node) => node.data.lines ?? [])).toHaveLength(
      new Set(first.nodes.filter((node) => node.type === 'agent').flatMap((node) => node.data.lines ?? [])).size,
    );
  });

  test('не подставляет демонстрационные поля базы и не вводит глобальное усечение вызовов', () => {
    const projection = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    const analyzeRole = projection.agent_phases![0].roles[0];
    const grouperRole = projection.agent_phases![1].roles[1];
    analyzeRole.invocations = Array.from({ length: 16 }, (_, index) => ({
      invocation_id: `a-${index}`, slot_id: `a-slot-${index}`, work_unit_id: `DIF-${index}`, status: 'running',
    }));
    analyzeRole.requested = 16;
    analyzeRole.running = 16;
    analyzeRole.queued = analyzeRole.completed = 0;
    grouperRole.invocations = Array.from({ length: 16 }, (_, index) => ({
      invocation_id: `g-${index}`, slot_id: `g-slot-${index}`, work_unit_id: `DIF-G-${index}`, status: 'queued',
    }));
    grouperRole.requested = 16;
    grouperRole.queued = 16;
    grouperRole.running = 0;
    const graph = buildDispatcherGraph(projection);
    expect(graph.nodes.filter((node) => node.type === 'agent')).toHaveLength(38);
    const analyzeAgents = graph.nodes.filter((node) => node.parentId === 'analyze-workers');
    expect(analyzeAgents.every((node) =>
      Number(node.position.x) >= 0
      && Number(node.position.y) >= 0
      && Number(node.position.x) + Number(node.style?.width) <= 274
      && Number(node.position.y) + Number(node.style?.height) <= 300)).toBe(true);
    const analyzeEdges = graph.edges.filter((edge) => edge.id.startsWith('agent-flow:agent:analyze-dif:'));
    expect(analyzeEdges).toHaveLength(16);
    expect(new Set(analyzeEdges.map((edge) => edge.targetHandle)).size).toBe(16);
    expect(JSON.stringify(graph)).not.toContain('v3.0.4');
    expect(JSON.stringify(graph)).not.toContain('28 731');
  });

  test('явно различает пустую, активную, остановленную и противоречивую проекции', () => {
    const empty = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    empty.items = {
      dif_queue: [], meaning_diffs: [], noise_diffs: [], proposals: [], mrqs: [], batches: [], decisions: [], approval_count: 0,
    };
    empty.agent_phases = [];
    empty.circuits = empty.circuits.map((circuit) => ({
      ...circuit,
      zones: circuit.zones?.map((zone) => ({ ...zone, state: 'waiting' })),
    }));
    const emptyGraph = buildDispatcherGraph(empty);
    expect(emptyGraph.nodes.filter((node) => node.type === 'agent')).toHaveLength(0);
    expect(emptyGraph.nodes.find((node) => node.id === 'analyze-workers')?.data.state).toBe('waiting');

    const activeGraph = buildDispatcherGraph(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    expect(activeGraph.nodes.find((node) => node.id === 'analyze-workers')?.data.active).toBe(true);
    expect(activeGraph.edges.filter((edge) => edge.animated).every((edge) => edge.source.startsWith('agent:'))).toBe(true);
    expect(activeGraph.edges.filter((edge) => edge.animated).map((edge) => edge.source)).toContain('agent:analyze-dif:analyzer:analyzer-2');

    const stopped = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    const stoppedRole = stopped.agent_phases![0].roles[0];
    stoppedRole.invocations = stoppedRole.invocations.map((item) => ({ ...item, status: 'interrupted' }));
    stoppedRole.running = stoppedRole.queued = stoppedRole.completed = 0;
    stoppedRole.interrupted = stoppedRole.requested;
    const stoppedGraph = buildDispatcherGraph(stopped);
    expect(stoppedGraph.nodes.find((node) => node.id.endsWith(':analyzer-1'))?.data.state).toBe('Прервано');
    expect(stoppedGraph.nodes.find((node) => node.id === 'analyze-workers')?.data.active).toBe(false);

    const contradictory = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    contradictory.agent_phases![0].roles[0].requested += 1;
    const contradictoryGraph = buildDispatcherGraph(contradictory);
    expect(contradictoryGraph.nodes.find((node) => node.id === 'analyze-workers')?.data).toMatchObject({
      state: 'error',
      active: false,
    });
    expect(contradictoryGraph.nodes.find((node) => node.id === 'analyze-workers')?.data.lines).toContain('Противоречивая проекция роли');
  });

  test('сохраняет порядок окна и показывает каждый вызов общего слота ровно один раз', () => {
    const projection = structuredClone(STATIC_COMPLETE_DISPATCHER_PROJECTION);
    const role = projection.agent_phases![0].roles[0];
    role.invocations = [
      { invocation_id: 'first', slot_id: 'shared', work_unit_id: 'DIF-1', status: 'running' },
      { invocation_id: 'second', slot_id: 'shared', work_unit_id: 'DIF-2', status: 'queued' },
      { invocation_id: 'third', slot_id: 'other', work_unit_id: 'DIF-3', status: 'queued' },
    ];
    role.requested = 3;
    role.running = 1;
    role.queued = 2;
    role.completed = 0;
    const graph = buildDispatcherGraph(projection);
    const analyzerNodes = graph.nodes.filter((node) => node.id.includes(':analyzer:'));
    expect(analyzerNodes).toHaveLength(2);
    expect(analyzerNodes[0].data.lines).toEqual(['first', 'second']);
    expect(analyzerNodes.flatMap((node) => node.data.lines ?? [])).toEqual(['first', 'second', 'third']);
  });
});
