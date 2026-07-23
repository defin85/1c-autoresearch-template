// Типы серверной операционной проекции диспетчера и маппинг в React Flow.
//
// Проекция присоединяется к снимку под ключём ``dispatcher`` и НЕ входит в
// канонические отпечатки. Узлы и рёбра вычисляются из фиксированных констант
// ``FIXED_NODES``/``FIXED_EDGES``; серверная проекция только уточняет
// состояние, агрегаты и свежесть каждого контура.

import { MarkerType, type Edge, type Node } from '@xyflow/react';
import { FIXED_EDGES, FIXED_NODES, NODE_BY_ID, type CircuitId, type FixedNode } from './nodes';

export interface CircuitLease {
  job_id: string;
  thread_id: string;
  work_unit_id: string;
  owner: string;
  acquired_at: string;
  renewed_at: string;
  state: string;
  summary: Record<string, unknown>;
}

export interface CircuitProjection {
  id: CircuitId;
  state: 'complete' | 'ready' | 'blocked' | 'unknown';
  leases?: CircuitLease[];
  aggregates?: Record<string, number | string>;
}

export interface DispatcherProjection {
  schema_version: string;
  revision: number;
  fresh_at: string;
  circuits: CircuitProjection[];
  jobs: Record<string, CircuitLease>;
  items: DispatcherItems;
  error?: string;
}

export interface DispatcherItems {
  dif_queue: DifCard[];
  meaning_diffs: DifCard[];
  noise_diffs: DifCard[];
  proposals: ProposalCard[];
  mrqs: MrqCard[];
  batches: BatchCard[];
  decisions: DecisionCard[];
  approval_count: number;
}

export interface DifCard {
  id: string;
  path: string;
  kind: string;
  state: string;
  intervention_kind?: string;
  object_scope?: string;
  target_coverage?: string;
  dependency_count?: number;
  blocker_codes?: string[];
}
export interface ProposalCard { id: string; job_id: string; kind: string; semantic_key: string; dif_ids: string[]; evidence_count: number; noise_count: number; mrq_id: string; created_at: string }
export interface MrqCard { id: string; title: string; semantic_key: string; state: string; dif_ids: string[]; evidence_count: number }
export interface BatchCard { id: string; mrq_ids: string[]; reason: string }
export interface DecisionCard { id: string; title: string; decision: string; target_solution: string; evidence_count: number; gap: boolean }

export const EMPTY_ITEMS: DispatcherItems = { dif_queue: [], meaning_diffs: [], noise_diffs: [], proposals: [], mrqs: [], batches: [], decisions: [], approval_count: 0 };

export const EMPTY_PROJECTION: DispatcherProjection = {
  schema_version: '1',
  revision: 0,
  fresh_at: '',
  circuits: [],
  jobs: {},
  items: EMPTY_ITEMS,
};

export function circuitById(projection: DispatcherProjection | undefined | null, id: CircuitId): CircuitProjection | undefined {
  if (!projection) return undefined;
  return projection.circuits.find((circuit) => circuit.id === id);
}

export function projectionStateColor(state: CircuitProjection['state'] | undefined): string {
  switch (state) {
    case 'complete':
      return '#2e7d32';
    case 'ready':
      return '#ed6c02';
    case 'blocked':
      return '#d32f2f';
    default:
      return '#9e9e9e';
  }
}

export function buildNodes(projection: DispatcherProjection | undefined | null, selectedId: string | null, animate: boolean): Node[] {
  const now = Date.now();
  return FIXED_NODES.map((fixed) => {
    const circuit = circuitById(projection, fixed.circuit);
    const lease = circuit?.leases?.[0];
    const fresh = lease ? isFresh(lease.renewed_at, now) : true;
    const isSelected = selectedId === fixed.id;
    return {
      id: fixed.id,
      type: 'dispatcherNode',
      position: fixed.position,
      data: {
        title: fixed.title,
        circuit: fixed.circuit,
        circuitState: circuit?.state ?? 'unknown',
        aggregates: circuit?.aggregates ?? {},
        lease,
        fresh,
        animate: animate && circuit?.state === 'ready',
        isSelected,
        items: projection?.items ?? EMPTY_ITEMS,
      },
      style: { width: fixed.width, height: 590, background: fixed.tint, border: `2px solid ${isSelected ? fixed.color : `${fixed.color}44`}`, borderRadius: 12 },
      draggable: false,
      connectable: false,
      selectable: true,
    } satisfies Node;
  });
}

export function buildEdges(animate: boolean): Edge[] {
  return FIXED_EDGES.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'smoothstep',
    animated: animate,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#607d8b', width: 18, height: 18 },
    style: { stroke: '#607d8b', strokeWidth: 2.2 },
  }));
}

export function isFresh(renewedAt: string, now: number): boolean {
  if (!renewedAt) return false;
  const parsed = Date.parse(renewedAt);
  if (Number.isNaN(parsed)) return false;
  // порог 10 секунд без серверного подтверждения (spec.md)
  return now - parsed < 10_000;
}

export function freshnessLabel(projection: DispatcherProjection | undefined | null): string {
  if (!projection) return 'без операционного состояния';
  if (projection.error) return 'операционное состояние недоступно';
  if (!projection.fresh_at) return 'без операционного состояния';
  const activeLeases = Object.values(projection.jobs);
  if (activeLeases.length === 0) return `подтверждено ${new Date(projection.fresh_at).toLocaleTimeString('ru-RU')}`;
  return activeLeases.every((lease) => isFresh(lease.renewed_at, Date.now())) ? 'данные актуальны' : 'данные несвежие';
}

export function fixedNodeById(id: string): FixedNode | undefined {
  return NODE_BY_ID[id];
}

export function allFixedNodes(): readonly FixedNode[] {
  return FIXED_NODES;
}
