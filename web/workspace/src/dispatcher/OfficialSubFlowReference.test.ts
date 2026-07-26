import { describe, expect, test } from 'vitest';
import { OFFICIAL_SUBFLOW_EDGES, OFFICIAL_SUBFLOW_NODES } from './OfficialSubFlowReference';

describe('OfficialSubFlowReference', () => {
  test('сохраняет связи официального примера внутри известных узлов', () => {
    const ids = new Set(OFFICIAL_SUBFLOW_NODES.map((node) => node.id));
    expect(OFFICIAL_SUBFLOW_NODES.filter((node) => node.type === 'group')).toHaveLength(2);
    expect(OFFICIAL_SUBFLOW_EDGES).toHaveLength(6);
    expect(OFFICIAL_SUBFLOW_EDGES.every((edge) => ids.has(edge.source) && ids.has(edge.target))).toBe(true);
  });
});
