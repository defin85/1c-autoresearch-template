import { useMemo } from 'react';
import { Box } from '@mui/material';
import { type Viewport } from '@xyflow/react';
import { EnrichedDispatcherGraph } from './EnrichedDispatcherGraph';
import { buildDispatcherNewGraph } from './dispatcherNewGraphModel';
import type { CircuitId, DispatcherProjection } from './projection';

export function DispatcherNew({
  projection,
  viewport,
  onMoveEnd,
  onActivate,
}: {
  projection: DispatcherProjection;
  viewport: Viewport;
  onMoveEnd: (viewport: Viewport) => void;
  onActivate: (circuitId: CircuitId, initiatorKey: string) => void;
}) {
  const model = useMemo(() => buildDispatcherNewGraph(projection), [projection]);
  return <Box data-testid="dispatcher-new-scroll" sx={{ height: 720, overflowX: 'auto', overflowY: 'hidden' }}>
    <Box sx={{ height: '100%', minWidth: { xs: 1955, xl: '100%' } }}>
      <EnrichedDispatcherGraph
        model={model}
        viewport={viewport}
        onMoveEnd={onMoveEnd}
        onActivate={onActivate}
        testId="dispatcher-new-canvas"
      />
    </Box>
  </Box>;
}
