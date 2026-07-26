import { memo, useCallback, useMemo } from 'react';
import { Box, Chip, Paper, Stack, Typography, useMediaQuery } from '@mui/material';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type NodeProps,
  type NodeTypes,
  type Edge,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { DispatcherGraphNodeData, DispatcherGraphModel, DispatcherGraphPort, DispatcherPortId } from './dispatcherGraphModel';

const handleStyle = (accent: string) => ({
  width: 8,
  height: 8,
  background: '#fff',
  border: `2px solid ${accent}`,
});

const FIXED_PORT_POSITIONS: Partial<Record<DispatcherPortId, { position: Position; offset?: string }>> = {
  left: { position: Position.Left },
  right: { position: Position.Right },
  top: { position: Position.Top },
  bottom: { position: Position.Bottom },
  'bottom-left': { position: Position.Bottom, offset: '33%' },
  'bottom-right': { position: Position.Bottom, offset: '67%' },
  'top-left': { position: Position.Top, offset: '33%' },
  'top-right': { position: Position.Top, offset: '67%' },
};

const portPosition = (id: DispatcherPortId) => {
  const fixed = FIXED_PORT_POSITIONS[id];
  if (fixed) return fixed;
  const match = /^(top|left)-(\d+)$/.exec(id);
  const ordinal = Number(match?.[2] ?? 1);
  return {
    position: match?.[1] === 'left' ? Position.Left : Position.Top,
    offset: `${ordinal * 100 / 17}%`,
  };
};

function NamedHandles({ accent, ports }: { accent: string; ports: DispatcherGraphPort[] }) {
  const style = handleStyle(accent);
  return <>{ports.map((port) => {
    const layout = portPosition(port.id);
    return <Handle
      key={`${port.type}:${port.id}`}
      id={port.id}
      type={port.type}
      position={layout.position}
      aria-hidden
      style={{
        ...style,
        ...(layout.position === Position.Top || layout.position === Position.Bottom ? { left: layout.offset } : {}),
        ...(layout.position === Position.Left || layout.position === Position.Right ? { top: layout.offset } : {}),
      }}
    />;
  })}</>;
}

const StageNode = memo(function StageNode({ data }: NodeProps<Node<DispatcherGraphNodeData>>) {
  return <Box sx={{ height: '100%', pointerEvents: 'none' }}>
    <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1.2, borderBottom: `1px solid ${data.accent}22` }}>
      <Typography sx={{ color: data.accent, fontSize: 17, fontWeight: 750, flex: 1 }}>{data.label}</Typography>
      <Chip size="small" label={data.state ?? 'unknown'} variant="outlined" sx={{ color: data.accent, borderColor: `${data.accent}88` }} />
    </Stack>
  </Box>;
});

const ZoneNode = memo(function ZoneNode({ id, data }: NodeProps<Node<DispatcherGraphNodeData>>) {
  return <Paper
    data-zone={id}
    variant="outlined"
    sx={{
      height: '100%',
      p: 0.8,
      borderColor: `${data.accent}88`,
      bgcolor: '#fff',
      overflow: 'hidden',
      ...(data.active && {
        '@keyframes dispatcherNodePulse': { '50%': { boxShadow: `0 0 0 4px ${data.accent}22` } },
        animation: 'dispatcherNodePulse 1.8s ease-in-out infinite',
        '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
      }),
    }}
  >
    <NamedHandles accent={data.accent} ports={data.ports ?? []} />
    <Stack direction="row" gap={0.5} alignItems="start">
      <Typography sx={{ fontSize: 11.5, fontWeight: 750, lineHeight: 1.2, flex: 1 }}>{data.label}</Typography>
      <Typography sx={{ color: data.accent, fontSize: 9.5 }}>{data.state}</Typography>
    </Stack>
    <Box
      {...(data.items?.length ? { tabIndex: 0, role: 'region', 'aria-label': `${data.label}: коллекция` } : {})}
      sx={{ maxHeight: 'calc(100% - 20px)', overflowY: data.items?.length ? 'auto' : 'hidden', overflowX: 'hidden' }}
    >
      {data.items?.length ? <Stack spacing={0.35} mt={0.7}>{data.items.map((item) =>
        <Typography key={item} noWrap title={item} sx={{ px: 0.45, py: 0.2, border: 1, borderColor: `${data.accent}33`, borderRadius: 0.7, color: data.accent, fontSize: 9.5 }}>{item}</Typography>,
      )}</Stack> : null}
      {data.subzones?.length ? <Stack direction="row" gap={0.45} mt={0.7}>{data.subzones.map((zone) =>
        <Box key={zone.id} data-zone={zone.id} sx={{ flex: 1, minWidth: 0, p: 0.4, border: 1, borderColor: `${data.accent}33`, borderRadius: 0.7 }}>
          <Typography sx={{ fontSize: 8.5, fontWeight: 700, lineHeight: 1.1 }}>{zone.label}</Typography>
          <Typography sx={{ color: data.accent, fontSize: 8 }}>{zone.state}</Typography>
        </Box>,
      )}</Stack> : null}
      {data.lines?.map((line) => <Typography key={line} sx={{ mt: 0.5, color: 'text.secondary', fontSize: 9.5 }}>{line}</Typography>)}
    </Box>
  </Paper>;
});

const AgentNode = memo(function AgentNode({ id, data }: NodeProps<Node<DispatcherGraphNodeData>>) {
  return <Paper
    data-agent-id={id}
    variant="outlined"
    sx={{
      height: '100%',
      px: 0.7,
      py: 0.45,
      borderColor: `${data.accent}99`,
      bgcolor: '#fff',
      ...(data.active && {
        '@keyframes dispatcherAgentPulse': { '50%': { boxShadow: `0 0 0 4px ${data.accent}22` } },
        animation: 'dispatcherAgentPulse 1.6s ease-in-out infinite',
        '@media (prefers-reduced-motion: reduce)': { animation: 'none' },
      }),
    }}
  >
    <NamedHandles accent={data.accent} ports={data.ports ?? []} />
    <Stack direction="row" alignItems="center" gap={0.5}>
      <Typography noWrap sx={{ fontSize: 10.5, fontWeight: 750, flex: 1 }}>{data.label}</Typography>
      <Typography noWrap sx={{ color: data.accent, fontSize: 9 }}>{data.state}</Typography>
    </Stack>
    <Stack spacing={0.1} mt={0.2} tabIndex={0} role="region" aria-label={`${data.label}: вызовы`} sx={{ maxHeight: 'calc(100% - 16px)', overflowY: 'auto' }}>
      {data.invocations?.map((invocation) => <Typography
        key={invocation.id}
        data-invocation-id={invocation.id}
        aria-label={`Вызов ${invocation.id}: слот ${data.label}, единица ${invocation.workUnitId}, состояние ${invocation.state}, ${invocation.updatedAt ? `подтверждено ${new Date(invocation.updatedAt).toLocaleTimeString('ru-RU')}` : 'время недоступно'}`}
        noWrap
        title={`${invocation.id}: ${invocation.workUnitId} · ${invocation.state} · ${invocation.updatedAt ? new Date(invocation.updatedAt).toLocaleTimeString('ru-RU') : 'время недоступно'}`}
        sx={{ color: 'text.secondary', fontSize: 8.5, lineHeight: 1.25 }}
      >
        {invocation.id} · {invocation.workUnitId} · {invocation.state} · {invocation.updatedAt ? new Date(invocation.updatedAt).toLocaleTimeString('ru-RU') : 'время недоступно'}
      </Typography>)}
    </Stack>
  </Paper>;
});

export const DISPATCHER_GRAPH_NODE_TYPES: NodeTypes = {
  stage: StageNode,
  zone: ZoneNode,
  agent: AgentNode,
};

export interface DispatcherGraphProps {
  model: DispatcherGraphModel;
  viewport?: Viewport;
  onMoveEnd?: (viewport: Viewport) => void;
  onActivate?: (circuitId: DispatcherGraphNodeData['circuitId'], initiatorKey: string) => void;
  testId?: string;
}

const DEFAULT_VIEWPORT: Viewport = { x: 4, y: 16, zoom: 0.9 };

export function DispatcherGraph({ model, viewport, onMoveEnd, onActivate, testId = 'dispatcher-graph' }: DispatcherGraphProps) {
  const reduceMotion = useMediaQuery('(prefers-reduced-motion: reduce)', { noSsr: true });
  const edges = useMemo(
    () => reduceMotion ? model.edges.map((edge) => edge.animated ? { ...edge, animated: false } : edge) : model.edges,
    [model.edges, reduceMotion],
  );
  const activate = useCallback((node: Node<DispatcherGraphNodeData>) => {
    if (node.data.interaction === 'open-circuit') onActivate?.(node.data.circuitId, node.id);
  }, [onActivate]);

  return <Box
    data-testid={testId}
    onKeyDownCapture={(event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const target = event.target as HTMLElement;
      const element = target.closest<HTMLElement>('[data-id]');
      if (target !== element) return;
      const node = model.nodes.find((item) => item.id === element?.dataset.id);
      if (!node) return;
      event.preventDefault();
      activate(node);
    }}
    sx={{
      height: '100%',
      minHeight: 720,
      border: 1,
      borderColor: 'divider',
      borderRadius: 2,
      bgcolor: '#fbfcfe',
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
      nodeTypes={DISPATCHER_GRAPH_NODE_TYPES}
      {...(viewport ? { viewport } : { defaultViewport: DEFAULT_VIEWPORT })}
      minZoom={0.9}
      maxZoom={1.4}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      panOnDrag
      zoomOnScroll
      onMoveEnd={(_, nextViewport) => onMoveEnd?.(nextViewport)}
      onNodeClick={(_, node) => activate(node as Node<DispatcherGraphNodeData>)}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} size={1} color="#e8edf5" />
      <Controls showInteractive={false} />
    </ReactFlow>
  </Box>;
}

export type { Edge, Node };
