import { memo, useCallback, useMemo } from 'react';
import { Box, Chip, Paper, Stack, Typography, useMediaQuery } from '@mui/material';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { AgentSlotProjection, CircuitId, DispatcherSelection } from './projection';

export type EnrichedNodeKind = 'stage' | 'card' | 'role';
export type EnrichedInteraction = 'none' | 'open-circuit';
export type EnrichedPortId =
  | 'left-top' | 'left' | 'left-bottom' | 'top'
  | 'right-top' | 'right' | 'right-bottom' | 'bottom'
  | 'in-top' | 'in-mid' | 'in-bottom'
  | 'out-top' | 'out-mid' | 'out-bottom';

export interface EnrichedInvocation {
  id: string;
  slotId?: string;
  workUnitId: string;
  state: string;
  updatedAt?: string;
}

export interface EnrichedNodeData extends Record<string, unknown> {
  kind: EnrichedNodeKind;
  title: string;
  zoneId?: string;
  detail?: string;
  state?: string;
  accent: string;
  active?: boolean;
  circuitId?: CircuitId;
  phaseId?: string;
  roleId?: string;
  runId?: string;
  interaction?: EnrichedInteraction;
  ports?: Array<{ id: EnrichedPortId; type: 'source' | 'target' }>;
  items?: string[];
  invocations?: EnrichedInvocation[];
  slots?: AgentSlotProjection[];
  selectableItems?: Array<{ id: string; kind: 'dif' | 'mrq' }>;
  collectionLabel?: string;
  subzones?: Array<{ id: string; title: string; state: string; detail: string }>;
}

export interface EnrichedDispatcherModel {
  nodes: Node<EnrichedNodeData>[];
  edges: Edge[];
}

const handleStyle = (accent: string) => ({
  width: 8,
  height: 8,
  background: '#fff',
  border: `2px solid ${accent}`,
});

const PORT_POSITION: Record<EnrichedPortId, { position: Position; offset?: string }> = {
  'left-top': { position: Position.Left, offset: '28%' },
  left: { position: Position.Left },
  'left-bottom': { position: Position.Left, offset: '72%' },
  top: { position: Position.Top },
  'right-top': { position: Position.Right, offset: '28%' },
  right: { position: Position.Right },
  'right-bottom': { position: Position.Right, offset: '72%' },
  bottom: { position: Position.Bottom },
  'in-top': { position: Position.Left, offset: '24%' },
  'in-mid': { position: Position.Left, offset: '50%' },
  'in-bottom': { position: Position.Left, offset: '76%' },
  'out-top': { position: Position.Right, offset: '24%' },
  'out-mid': { position: Position.Right, offset: '50%' },
  'out-bottom': { position: Position.Right, offset: '76%' },
};

const DEFAULT_PORTS: EnrichedNodeData['ports'] = [
  { id: 'left-top', type: 'target' },
  { id: 'left', type: 'target' },
  { id: 'left-bottom', type: 'target' },
  { id: 'top', type: 'target' },
  { id: 'right-top', type: 'source' },
  { id: 'right', type: 'source' },
  { id: 'right-bottom', type: 'source' },
  { id: 'bottom', type: 'source' },
];

function NamedHandles({ accent, ports = DEFAULT_PORTS }: { accent: string; ports?: EnrichedNodeData['ports'] }) {
  const style = handleStyle(accent);
  return <>{ports?.map((port) => {
    const layout = PORT_POSITION[port.id];
    return <Handle
      key={`${port.type}:${port.id}`}
      id={port.id}
      type={port.type}
      position={layout.position}
      aria-hidden
      style={{
        ...style,
        ...(layout.position === Position.Left || layout.position === Position.Right ? { top: layout.offset } : {}),
      }}
    />;
  })}</>;
}

const StageNode = memo(function StageNode({ data }: NodeProps<Node<EnrichedNodeData>>) {
  return <Stack direction="row" alignItems="center" sx={{ px: 1.6, py: 1.35 }}>
    <Typography sx={{ color: data.accent, fontSize: 17, fontWeight: 750, flex: 1 }}>{data.title}</Typography>
    {data.state && <Typography sx={{ color: data.state.startsWith('Ошибка') ? 'error.main' : data.accent, fontSize: 10 }}>{data.state}</Typography>}
  </Stack>;
});

const CardNode = memo(function CardNode({ id, data }: NodeProps<Node<EnrichedNodeData>>) {
  return <Paper
    data-zone={data.zoneId ?? id}
    data-content-inset
    variant="outlined"
    sx={{
      width: '100%',
      minWidth: 0,
      maxWidth: '100%',
      height: '100%',
      boxSizing: 'border-box',
      p: 1,
      borderColor: `${data.accent}99`,
      bgcolor: '#fff',
      overflow: 'hidden',
      ...(data.active && {
        '@keyframes enrichedNodePulse': { '50%': { boxShadow: `0 0 0 5px ${data.accent}22` } },
        animation: 'enrichedNodePulse 1.8s ease-in-out infinite',
        '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
      }),
    }}
  >
    <NamedHandles accent={data.accent} ports={data.ports} />
    <Stack spacing={0.4} sx={{ height: '100%', minHeight: 0 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 750, lineHeight: 1.15 }}>{data.title}</Typography>
      {data.state && <Chip label={data.state} size="small" sx={{ alignSelf: 'flex-start', height: 18, color: data.accent, '& .MuiChip-label': { px: 0.7, fontSize: 9.5 } }} />}
      {data.subzones?.length ? <Stack
        spacing={0.5}
        tabIndex={0}
        role="region"
        aria-label={`${data.title}: вложенные зоны`}
        sx={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
      >
        {data.detail && <Typography sx={{ fontSize: 10.5, color: 'text.secondary', lineHeight: 1.3, overflowWrap: 'anywhere' }}>{data.detail}</Typography>}
        {data.subzones.map((zone) => <Box
          key={zone.id}
          data-zone={zone.id}
          data-content-inset
          sx={{ p: 0.75, border: 1, borderColor: `${data.accent}44`, borderRadius: 0.7, bgcolor: '#fff' }}
        >
          <Stack gap={0.1}>
            <Typography sx={{ fontSize: 9.5, fontWeight: 750, overflowWrap: 'anywhere' }}>{zone.title}</Typography>
            <Typography sx={{ fontSize: 8.5, color: zone.state === 'Ошибка' ? 'error.main' : data.accent }}>{zone.state}</Typography>
          </Stack>
          <Typography sx={{ fontSize: 8.5, color: 'text.secondary' }}>{zone.detail}</Typography>
        </Box>)}
      </Stack> : null}
      {data.detail && !data.subzones?.length && <Typography
        tabIndex={0}
        role="region"
        aria-label={`${data.title}: содержимое`}
        sx={{ flex: 1, minHeight: 0, overflowY: 'auto', fontSize: 10.5, color: 'text.secondary', lineHeight: 1.3, overflowWrap: 'anywhere' }}
      >{data.detail}</Typography>}
      {data.items?.length ? <Stack
        spacing={0.35}
        tabIndex={0}
        role="region"
        aria-label={data.collectionLabel ?? `${data.title}: коллекция`}
        sx={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
      >
        {data.items.map((item) => <Typography key={item} noWrap title={item} sx={{ fontSize: 9.5, color: 'text.secondary' }}>{item}</Typography>)}
      </Stack> : null}
    </Stack>
  </Paper>;
});

const RoleNode = memo(function RoleNode({ id, data }: NodeProps<Node<EnrichedNodeData>>) {
  const invocations = data.invocations ?? [];
  const running = invocations.filter((invocation) => invocation.state === 'Выполняется').length;
  const completed = invocations.filter((invocation) => invocation.state === 'Завершено').length;
  const failed = invocations.filter((invocation) => invocation.state === 'Ошибка').length;
  return <Box
    data-zone={data.zoneId ?? id}
    data-content-inset
    sx={{
      height: '100%',
      boxSizing: 'border-box',
      p: 1,
      position: 'relative',
      display: 'flex',
      flexDirection: 'column',
      border: 1,
      borderColor: `${data.accent}99`,
      borderRadius: 1,
      bgcolor: '#fff',
      ...(data.active && {
        '@keyframes enrichedRolePulse': { '50%': { boxShadow: `0 0 0 5px ${data.accent}22` } },
        animation: 'enrichedRolePulse 1.6s ease-in-out infinite',
        '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
      }),
    }}
  >
    <NamedHandles accent={data.accent} ports={data.ports} />
    <Box component="button" type="button" data-dispatcher-kind="role" data-circuit-id={data.circuitId}
      data-phase-id={data.phaseId} data-role-id={data.roleId}
      sx={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%', border: 0, bgcolor: 'transparent', p: 0, textAlign: 'left', cursor: 'pointer' }}>
      <Typography sx={{ fontSize: 12.5, fontWeight: 750, lineHeight: 1.15, overflowWrap: 'anywhere' }}>{data.title}</Typography>
      <Typography noWrap sx={{ color: data.accent, fontSize: 10, mt: 0.25 }}>{data.state}</Typography>
      <Stack spacing={0.35} mt={1}>
        <Typography noWrap sx={{ fontSize: 10.5 }}>Вызовов: {invocations.length}</Typography>
        <Typography noWrap sx={{ fontSize: 10.5 }}>Выполняется: {running}</Typography>
        <Typography noWrap sx={{ fontSize: 10.5 }}>Завершено: {completed}</Typography>
        <Typography noWrap sx={{ fontSize: 10.5, color: failed ? 'error.main' : 'text.secondary' }}>Ошибок: {failed}</Typography>
      </Stack>
    </Box>
  </Box>;
});

export const ENRICHED_NODE_TYPES: NodeTypes = {
  stage: StageNode,
  card: CardNode,
  role: RoleNode,
  spec: CardNode,
};

export interface EnrichedDispatcherGraphProps {
  model: EnrichedDispatcherModel;
  viewport?: Viewport;
  onMoveEnd?: (viewport: Viewport) => void;
  onActivate?: (selection: DispatcherSelection, initiator: HTMLElement) => void;
  fitView?: boolean;
  testId?: string;
}

const DEFAULT_VIEWPORT: Viewport = { x: 4, y: 10, zoom: 0.9 };

export function EnrichedDispatcherGraph({
  model,
  viewport,
  onMoveEnd,
  onActivate,
  fitView = false,
  testId = 'enriched-dispatcher-graph',
}: EnrichedDispatcherGraphProps) {
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)', { noSsr: true });
  const edges = useMemo(
    () => reduceMotion ? model.edges.map((edge) => edge.animated ? { ...edge, animated: false } : edge) : model.edges,
    [model.edges, reduceMotion],
  );
  const activate = useCallback((node: Node<EnrichedNodeData>, initiator: HTMLElement) => {
    if (node.data.interaction === 'open-circuit' && node.data.circuitId) {
      const queueId = ({
        'dif-queue': 'dif-queue',
        'analysis-queue': 'dif-queue',
        'semantic-dif': 'meaning-diffs',
        'technical-noise': 'noise-diffs',
        'semantic-queue': 'meaning-diffs',
        proposal: 'proposals',
        publication: 'mrq-queue',
        'batch-input': 'mrq-queue',
        'batch-output': 'batches',
        'mrq-queue': 'mrq-queue',
        'target-db': 'approvals',
        results: 'decisions',
      } as Record<string, string>)[node.id];
      onActivate?.(queueId
        ? { kind: 'queue', circuitId: node.data.circuitId, queueId }
        : { kind: 'circuit', circuitId: node.data.circuitId }, initiator);
    }
  }, [onActivate]);

  return <Box
    data-testid={testId}
    onKeyDownCapture={(event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const target = (event.target as HTMLElement).closest<HTMLElement>('.react-flow__node[data-id]');
      if (!target) return;
      const node = model.nodes.find((candidate) => candidate.id === target.dataset.id);
      if (!node || node.data.interaction !== 'open-circuit') return;
      event.preventDefault();
      event.stopPropagation();
      activate(node, target);
    }}
    onClickCapture={(event) => {
      const target = (event.target as HTMLElement).closest<HTMLElement>('[data-dispatcher-kind][data-circuit-id]');
      if (!target) return;
      event.stopPropagation();
      const common = {
        circuitId: target.dataset.circuitId as CircuitId,
        phaseId: target.dataset.phaseId!,
        roleId: target.dataset.roleId!,
      };
      const selection: DispatcherSelection = target.dataset.dispatcherKind === 'role'
        ? { kind: 'role', ...common }
        : target.dataset.dispatcherKind === 'invocation'
          ? { kind: 'invocation', ...common, slotId: target.dataset.agentSlotId!, invocationId: target.dataset.invocationId!, ...(target.dataset.runId ? { runId: target.dataset.runId } : {}) }
          : { kind: 'slot', ...common, slotId: target.dataset.agentSlotId!, ...(target.dataset.runId ? { runId: target.dataset.runId } : {}) };
      onActivate?.(selection, target);
    }}
    sx={{
      height: '100%',
      border: 1,
      borderColor: 'divider',
      borderRadius: 2,
      overflow: 'hidden',
      '& .react-flow__edge-interaction': { pointerEvents: 'none !important' },
      '@media (prefers-reduced-motion: reduce)': {
        '& .react-flow__edge.animated path': { animation: 'none !important' },
      },
    }}
  >
    <ReactFlow
      nodes={model.nodes}
      edges={edges}
      nodeTypes={ENRICHED_NODE_TYPES}
      {...(viewport ? { viewport } : fitView ? { fitView: true, fitViewOptions: { padding: 0.02 } } : { defaultViewport: DEFAULT_VIEWPORT })}
      minZoom={0.45}
      maxZoom={1.4}
      nodesDraggable={false}
      nodesConnectable={false}
      edgesFocusable={false}
      elementsSelectable
      panOnDrag
      zoomOnScroll
      onMoveEnd={(_, nextViewport) => onMoveEnd?.(nextViewport)}
      onNodeClick={(event, node) => activate(node as Node<EnrichedNodeData>, event.currentTarget as HTMLElement)}
      proOptions={{ hideAttribution: true }}
    >
      <Controls showInteractive={false} />
      <Background color="#e6e6e6" />
    </ReactFlow>
  </Box>;
}
