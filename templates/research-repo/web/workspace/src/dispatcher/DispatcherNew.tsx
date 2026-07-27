import { useMemo } from 'react';
import { Box } from '@mui/material';
import { type Viewport } from '@xyflow/react';
import { EnrichedDispatcherGraph } from './EnrichedDispatcherGraph';
import { buildDispatcherNewGraph } from './dispatcherNewGraphModel';
import type { DispatcherProjection, DispatcherSelection } from './projection';

export function DispatcherNew({
  projection,
  viewport,
  onMoveEnd,
  onActivate,
}: {
  projection: DispatcherProjection;
  viewport: Viewport;
  onMoveEnd: (viewport: Viewport) => void;
  onActivate: (selection: DispatcherSelection, initiator: HTMLElement) => void;
}) {
  const model = useMemo(() => buildDispatcherNewGraph(projection), [projection]);
  return <Box data-testid="dispatcher-new-scroll" sx={{ width: '100%', minWidth: 0, maxWidth: '100%', height: 720, overflow: 'hidden' }}>
    <EnrichedDispatcherGraph
      model={model}
      viewport={viewport}
      onMoveEnd={onMoveEnd}
      onActivate={onActivate}
      testId="dispatcher-new-canvas"
    />
  </Box>;
}
