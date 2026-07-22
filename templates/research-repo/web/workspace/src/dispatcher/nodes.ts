export type CircuitId = 'prepare-diffs' | 'analyze-dif' | 'form-mrq' | 'decide-target';

export interface FixedNode {
  id: CircuitId;
  circuit: CircuitId;
  title: string;
  position: { x: number; y: number };
  width: number;
  color: string;
  tint: string;
}

export const FIXED_NODES: FixedNode[] = [
  { id: 'prepare-diffs', circuit: 'prepare-diffs', title: 'Подготовка различий', position: { x: 0, y: 0 }, width: 330, color: '#1976d2', tint: '#f4f8ff' },
  { id: 'analyze-dif', circuit: 'analyze-dif', title: 'Анализ DIF', position: { x: 360, y: 0 }, width: 430, color: '#1976d2', tint: '#f7faff' },
  { id: 'form-mrq', circuit: 'form-mrq', title: 'Формирование MRQ', position: { x: 820, y: 0 }, width: 570, color: '#6d3be7', tint: '#faf8ff' },
  { id: 'decide-target', circuit: 'decide-target', title: 'Исследование целевой базы', position: { x: 1420, y: 0 }, width: 470, color: '#0097a7', tint: '#f4fcfd' },
];

export const FIXED_EDGES = [
  { id: 'prepare-analyze', source: 'prepare-diffs', target: 'analyze-dif' },
  { id: 'analyze-form', source: 'analyze-dif', target: 'form-mrq' },
  { id: 'form-decide', source: 'form-mrq', target: 'decide-target' },
] as const;

export const NODE_BY_ID: Record<string, FixedNode> = Object.fromEntries(FIXED_NODES.map((node) => [node.id, node]));
