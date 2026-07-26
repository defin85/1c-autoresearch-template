import { describe, expect, test } from 'vitest';
import { ENRICHED_SUBFLOW_EDGES, ENRICHED_SUBFLOW_NODES } from './EnrichedSubFlowReference';

describe('EnrichedSubFlowReference', () => {
  test('сохраняет пять этапов и целостные связи статической схемы', () => {
    const ids = new Set(ENRICHED_SUBFLOW_NODES.map((node) => node.id));
    const stages = ENRICHED_SUBFLOW_NODES.filter((node) => node.type === 'stage');
    expect(stages).toHaveLength(5);
    expect(ENRICHED_SUBFLOW_NODES.filter((node) => node.data.active).length).toBeGreaterThan(0);
    expect(ENRICHED_SUBFLOW_EDGES.filter((edge) => edge.animated).length).toBeGreaterThan(0);
    expect(ENRICHED_SUBFLOW_EDGES.every((edge) => ids.has(edge.source) && ids.has(edge.target))).toBe(true);
  });
});
