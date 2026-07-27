import { describe, expect, test } from 'vitest';
import { EMPTY_PROJECTION, VISIBLE_DECISION_LIMIT, VISIBLE_INVOCATION_LIMIT, VISIBLE_QUEUE_LIMIT, circuitById, freshnessLabel, isFresh, roleProgress, visibleWindow, type AgentRoleProjection, type CircuitId } from './projection';

describe('dispatcher projection', () => {
  test('empty projection yields neutral freshness', () => {
    expect(freshnessLabel(EMPTY_PROJECTION)).toBe('без операционного состояния');
    expect(freshnessLabel(undefined)).toBe('без операционного состояния');
  });

  test('error projection reports unavailability', () => {
    expect(freshnessLabel({ ...EMPTY_PROJECTION, error: 'boom' })).toBe('операционное состояние недоступно');
  });

  test('completed projection shows confirmation time regardless of age', () => {
    const confirmed = new Date(Date.now() - 30_000).toISOString();
    expect(freshnessLabel({ ...EMPTY_PROJECTION, fresh_at: confirmed })).toMatch(/^подтверждено /);
  });

  test('active projection (>10s) is reported as stale', () => {
    const stale = new Date(Date.now() - 30_000).toISOString();
    expect(freshnessLabel({ ...EMPTY_PROJECTION, fresh_at: stale, jobs: { 'analyze-dif': { job_id: 'analyze-dif', thread_id: 't', work_unit_id: 'DIF-1', owner: 'u', acquired_at: stale, renewed_at: stale, state: 'running', summary: {} } } })).toBe('данные несвежие');
  });

  test('isFresh respects 10-second threshold', () => {
    const now = Date.parse('2026-07-21T12:00:00Z');
    expect(isFresh(new Date(now - 9_999).toISOString(), now)).toBe(true);
    expect(isFresh(new Date(now - 10_000).toISOString(), now)).toBe(false);
    expect(isFresh('', now)).toBe(false);
  });

  test('circuitById finds existing circuit', () => {
    const projection = { ...EMPTY_PROJECTION, circuits: [{ id: 'form-mrq' as CircuitId, state: 'ready' as const, aggregates: { active_mrq_count: 3 } }] };
    const circuit = circuitById(projection, 'form-mrq');
    expect(circuit?.state).toBe('ready');
    expect(circuit?.aggregates?.active_mrq_count).toBe(3);
    expect(circuitById(projection, 'decide-target')).toBeUndefined();
  });

  test('visible windows preserve server order at the fixed limits', () => {
    const values = Array.from({ length: 20 }, (_, index) => index);
    expect(visibleWindow(values, VISIBLE_QUEUE_LIMIT)).toEqual([0, 1, 2, 3]);
    expect(visibleWindow(values, VISIBLE_DECISION_LIMIT)).toEqual([0, 1, 2]);
    expect(visibleWindow(values, VISIBLE_INVOCATION_LIMIT)).toEqual(values.slice(0, 16));
  });

  test('role progress requires an exhaustive partition and separates capacity', () => {
    const role: AgentRoleProjection = {
      role_id: 'analyzer', agent_profile: 'local', configured_slots: 4, requested: 10,
      running: 2, queued: 1, completed: 4, failed: 1, cancelled: 1, interrupted: 1,
      invocations: [],
    };
    expect(roleProgress(role)).toEqual({ valid: true, percent: 70, processed: 7, freeCapacity: 2 });
    expect(roleProgress({ ...role, requested: 11 })).toEqual({ valid: false, percent: undefined, processed: 7, freeCapacity: 2 });
    expect(roleProgress({ ...role, requested: 0, running: 0, queued: 0, completed: 0, failed: 0, cancelled: 0, interrupted: 0 })).toEqual({ valid: true, percent: undefined, processed: 0, freeCapacity: 4 });
  });
});
