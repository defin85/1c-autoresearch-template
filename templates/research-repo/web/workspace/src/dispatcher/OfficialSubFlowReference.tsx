import { useCallback } from 'react';
import {
  addEdge,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import { Box, Stack, Typography } from '@mui/material';
import '@xyflow/react/dist/style.css';

export const OFFICIAL_SUBFLOW_NODES: Node[] = [
  { id: '1', type: 'input', data: { label: 'Node 0' }, position: { x: 250, y: 5 } },
  { id: '2', type: 'group', data: { label: 'Group A' }, position: { x: 100, y: 100 }, style: { width: 200, height: 200 } },
  { id: '2a', data: { label: 'Node A.1' }, position: { x: 10, y: 50 }, parentId: '2' },
  { id: '3', data: { label: 'Node 1' }, position: { x: 320, y: 100 } },
  { id: '4', type: 'group', data: { label: 'Group B' }, position: { x: 320, y: 200 }, style: { width: 300, height: 300 } },
  { id: '4a', data: { label: 'Node B.1' }, position: { x: 15, y: 65 }, parentId: '4', extent: 'parent' },
  {
    id: '4b',
    data: { label: 'Group B.A' },
    position: { x: 15, y: 120 },
    style: { backgroundColor: 'rgba(255, 0, 255, 0.2)', height: 150, width: 270 },
    parentId: '4',
  },
  { id: '4b1', data: { label: 'Node B.A.1' }, position: { x: 20, y: 40 }, parentId: '4b' },
  { id: '4b2', data: { label: 'Node B.A.2' }, position: { x: 100, y: 100 }, parentId: '4b' },
];

export const OFFICIAL_SUBFLOW_EDGES: Edge[] = [
  { id: 'e1-3', source: '1', target: '3' },
  { id: 'e2a-4a', source: '2a', target: '4a' },
  { id: 'e3-4b', source: '3', target: '4b' },
  { id: 'e4a-4b1', source: '4a', target: '4b1' },
  { id: 'e4a-4b2', source: '4a', target: '4b2' },
  { id: 'e4b1-4b2', source: '4b1', target: '4b2' },
];

export function OfficialSubFlowReference() {
  const [nodes, , onNodesChange] = useNodesState(OFFICIAL_SUBFLOW_NODES);
  const [edges, setEdges, onEdgesChange] = useEdgesState(OFFICIAL_SUBFLOW_EDGES);
  const onConnect = useCallback((connection: Connection) => setEdges((current) => addEdge(connection, current)), [setEdges]);

  return <Stack spacing={1.2}>
    <Box>
      <Typography variant="h6" fontWeight={750}>Базовый пример React Flow: Sub Flow</Typography>
      <Typography variant="body2" color="text.secondary">Официальный пример без компонентов и правил диспетчера.</Typography>
    </Box>
    <Box data-testid="official-subflow-reference" sx={{ height: 690, border: 1, borderColor: 'divider', borderRadius: 2 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        fitView
        colorMode="light"
      >
        <MiniMap />
        <Controls />
        <Background color="#e6e6e6" />
      </ReactFlow>
    </Box>
  </Stack>;
}
