import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MarkerType } from '@xyflow/react';
import { describe, expect, test, vi } from 'vitest';
import { EnrichedDispatcherGraph, type EnrichedDispatcherModel } from './EnrichedDispatcherGraph';

const model: EnrichedDispatcherModel = {
  nodes: [
    {
      id: 'stage',
      type: 'stage',
      position: { x: 0, y: 0 },
      style: { width: 300, height: 680 },
      data: { kind: 'stage', title: 'Этап', accent: '#1976d2', circuitId: 'analyze-dif', interaction: 'open-circuit' },
    },
    {
      id: 'role',
      type: 'role',
      parentId: 'stage',
      extent: 'parent',
      position: { x: 20, y: 70 },
      style: { width: 200, height: 200 },
      data: {
        kind: 'role',
        title: 'Анализаторы',
        accent: '#1976d2',
        circuitId: 'analyze-dif',
        interaction: 'open-circuit',
        ports: [{ id: 'in-mid', type: 'target' }, { id: 'out-mid', type: 'source' }],
        invocations: [
          { id: 'invoke-1', slotId: 'slot-1', workUnitId: '<img src=x onerror=alert(1)>', state: 'Выполняется' },
          { id: 'invoke-2', slotId: 'slot-1', workUnitId: 'DIF-2', state: 'Ожидает' },
          { id: 'invoke-3', slotId: 'slot-2', workUnitId: 'DIF-3', state: 'Завершено' },
        ],
      },
    },
  ],
  edges: [{
    id: 'edge',
    source: 'stage',
    target: 'role',
    selectable: false,
    focusable: false,
    markerEnd: { type: MarkerType.ArrowClosed },
  }],
};

describe('EnrichedDispatcherGraph', () => {
  test('активирует этап и фактического агента ровно по одному разу', () => {
    const onActivate = vi.fn();
    const { container } = render(<EnrichedDispatcherGraph model={model} onActivate={onActivate} />);
    const stage = screen.getByTestId('rf__node-stage');
    fireEvent.click(stage);
    expect(onActivate).toHaveBeenLastCalledWith('analyze-dif', 'new:stage');
    fireEvent.keyDown(stage, { key: 'Enter' });
    expect(onActivate).toHaveBeenLastCalledWith('analyze-dif', 'new:stage');
    fireEvent.keyDown(screen.getByTestId('rf__node-role'), { key: ' ' });
    expect(onActivate).toHaveBeenLastCalledWith('analyze-dif', 'new:role');
    const slot = container.querySelector<HTMLElement>('[data-dispatcher-initiator="new-slot:role:slot-1"]')!;
    fireEvent.click(slot);
    expect(onActivate).toHaveBeenLastCalledWith('analyze-dif', 'new-slot:role:slot-1');
    expect(onActivate).toHaveBeenCalledTimes(4);
    expect(container.querySelectorAll('[data-agent-slot-id]')).toHaveLength(2);
    expect(container.querySelectorAll('[data-invocation-id]')).toHaveLength(3);
  });

  test('оставляет порты декоративными и выводит проекцию только текстом', () => {
    cleanup();
    render(<EnrichedDispatcherGraph model={model} />);
    expect([...document.querySelectorAll('.react-flow__handle')].every((handle) => handle.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(screen.getAllByText(/<img src=x onerror=alert\(1\)>/)).not.toHaveLength(0);
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('.react-flow__edge[tabindex]')).toBeNull();
    const role = document.querySelector<HTMLElement>('[data-zone="role"]')!;
    expect(getComputedStyle(role).borderTopStyle).toBe('solid');
    expect(getComputedStyle(role).backgroundColor).toBe('rgb(255, 255, 255)');
    expect(getComputedStyle(role).overflow).not.toBe('hidden');
  });
});
