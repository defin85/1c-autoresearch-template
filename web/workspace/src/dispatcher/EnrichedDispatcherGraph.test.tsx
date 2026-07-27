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
  test('активирует этап и сводную карточку роли ровно по одному разу', () => {
    const onActivate = vi.fn();
    const { container } = render(<EnrichedDispatcherGraph model={model} onActivate={onActivate} />);
    const stage = screen.getByTestId('rf__node-stage');
    fireEvent.click(stage);
    expect(onActivate).toHaveBeenLastCalledWith({ kind: 'circuit', circuitId: 'analyze-dif' }, expect.any(HTMLElement));
    fireEvent.keyDown(stage, { key: 'Enter' });
    expect(onActivate).toHaveBeenLastCalledWith({ kind: 'circuit', circuitId: 'analyze-dif' }, expect.any(HTMLElement));
    const role = container.querySelector<HTMLElement>('[data-dispatcher-kind="role"]')!;
    fireEvent.click(role);
    expect(onActivate).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'role', circuitId: 'analyze-dif' }), role);
    expect(onActivate).toHaveBeenCalledTimes(3);
    expect(container.querySelectorAll('[data-dispatcher-kind="slot"]')).toHaveLength(0);
    expect(container.querySelectorAll('[data-invocation-id]')).toHaveLength(0);
    expect(screen.getByText('Вызовов: 3')).toBeInTheDocument();
    expect(screen.getByText('Выполняется: 1')).toBeInTheDocument();
    expect(screen.getByText('Завершено: 1')).toBeInTheDocument();
  });

  test('оставляет порты декоративными и не выводит детали вызовов', () => {
    cleanup();
    render(<EnrichedDispatcherGraph model={model} />);
    expect([...document.querySelectorAll('.react-flow__handle')].every((handle) => handle.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(screen.queryByText(/<img src=x onerror=alert\(1\)>/)).toBeNull();
    expect(document.querySelector('img')).toBeNull();
    expect(document.querySelector('.react-flow__edge[tabindex]')).toBeNull();
    const role = document.querySelector<HTMLElement>('[data-zone="role"]')!;
    expect(getComputedStyle(role).borderTopStyle).toBe('solid');
    expect(getComputedStyle(role).backgroundColor).toBe('rgb(255, 255, 255)');
    expect(getComputedStyle(role).overflow).not.toBe('hidden');
  });
});
