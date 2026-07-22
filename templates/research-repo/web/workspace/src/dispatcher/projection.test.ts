import { describe, expect, test } from 'vitest';
import { EMPTY_PROJECTION, buildEdges, buildNodes, circuitById, freshnessLabel, isFresh, projectionStateColor } from './projection';
import { FIXED_EDGES, FIXED_NODES, type CircuitId } from './nodes';

describe('dispatcher projection', () => {
  test('empty projection yields neutral freshness', () => {
    expect(freshnessLabel(EMPTY_PROJECTION)).toBe('без операционного состояния');
    expect(freshnessLabel(undefined)).toBe('без операционного состояния');
  });

  test('error projection reports unavailability', () => {
    expect(freshnessLabel({ ...EMPTY_PROJECTION, error: 'boom' })).toBe('операционное состояние недоступно');
  });

  test('completed projection shows confirmation time regardless of age', () => {
    const fresh = new Date(Date.now() - 1_000).toISOString();
    expect(freshnessLabel({ ...EMPTY_PROJECTION, fresh_at: fresh })).toMatch(/^подтверждено /);
  });

  test('active projection (>10s) is reported as stale', () => {
    const stale = new Date(Date.now() - 30_000).toISOString();
    expect(freshnessLabel({ ...EMPTY_PROJECTION, fresh_at: stale, jobs: { 'discover-mrq': { job_id: 'discover-mrq', thread_id: 't', work_unit_id: 'DIF-1', owner: 'u', acquired_at: stale, renewed_at: stale, state: 'running', summary: {} } } })).toBe('данные несвежие');
  });

  test('isFresh respects 10-second threshold', () => {
    const now = Date.parse('2026-07-21T12:00:00Z');
    expect(isFresh(new Date(now - 5_000).toISOString(), now)).toBe(true);
    expect(isFresh(new Date(now - 11_000).toISOString(), now)).toBe(false);
    expect(isFresh('', now)).toBe(false);
  });

  test('projectionStateColor maps each state', () => {
    expect(projectionStateColor('complete')).toBe('#2e7d32');
    expect(projectionStateColor('ready')).toBe('#ed6c02');
    expect(projectionStateColor('blocked')).toBe('#d32f2f');
    expect(projectionStateColor('unknown')).toBe('#9e9e9e');
  });

  test('buildNodes returns fixed nodes with disabled drag/connect', () => {
    const nodes = buildNodes(EMPTY_PROJECTION, null, false);
    expect(nodes).toHaveLength(FIXED_NODES.length);
    for (const node of nodes) {
      expect(node.draggable).toBe(false);
      expect(node.connectable).toBe(false);
      expect(node.selectable).toBe(true);
      expect(node.type).toBe('dispatcherNode');
    }
  });

  test('buildNodes marks selected node', () => {
    const nodes = buildNodes(EMPTY_PROJECTION, 'prepare-diffs', false);
    const selected = nodes.find((node) => node.id === 'prepare-diffs');
    expect(selected).toBeDefined();
    expect((selected!.data as { isSelected: boolean }).isSelected).toBe(true);
  });

  test('buildEdges returns fixed edges with smoothstep type', () => {
    const edges = buildEdges(false);
    expect(edges).toHaveLength(FIXED_EDGES.length);
    for (const edge of edges) {
      expect(edge.type).toBe('smoothstep');
      expect(edge.animated).toBe(false);
    }
  });

  test('buildEdges respects animate flag for reduced-motion off', () => {
    const edges = buildEdges(true);
    expect(edges.every((edge) => edge.animated)).toBe(true);
  });

  test('circuitById finds existing circuit', () => {
    const projection = { ...EMPTY_PROJECTION, circuits: [{ id: 'form-mrq' as CircuitId, state: 'ready' as const, aggregates: { active_mrq_count: 3 } }] };
    const circuit = circuitById(projection, 'form-mrq');
    expect(circuit?.state).toBe('ready');
    expect(circuit?.aggregates?.active_mrq_count).toBe(3);
    expect(circuitById(projection, 'decide-target')).toBeUndefined();
  });

  test('fixed nodes cover exactly four circuits', () => {
    const circuits = new Set(FIXED_NODES.map((node) => node.circuit));
    expect(circuits).toEqual(new Set(['prepare-diffs', 'analyze-dif', 'form-mrq', 'decide-target']));
  });

  test('fixed edges reference only known nodes', () => {
    const known = new Set(FIXED_NODES.map((node) => node.id));
    for (const edge of FIXED_EDGES) {
      expect(known.has(edge.source)).toBe(true);
      expect(known.has(edge.target)).toBe(true);
    }
  });

  test('projection with error does not break node rendering', () => {
    const nodes = buildNodes({ ...EMPTY_PROJECTION, error: 'store gone' }, null, false);
    expect(nodes).toHaveLength(FIXED_NODES.length);
    for (const node of nodes) {
      expect((node.data as { circuitState: string }).circuitState).toBe('unknown');
    }
  });
});
