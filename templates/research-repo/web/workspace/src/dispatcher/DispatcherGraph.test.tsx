import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, test, vi } from 'vitest';
import { DispatcherGraph, DISPATCHER_GRAPH_NODE_TYPES } from './DispatcherGraph';
import { STATIC_COMPLETE_DISPATCHER_GRAPH } from './dispatcherGraphModel';

describe('DispatcherGraph', () => {
  test('использует общий реестр узлов и активирует контур клавиатурой', () => {
    const onActivate = vi.fn();
    render(<DispatcherGraph model={STATIC_COMPLETE_DISPATCHER_GRAPH} onActivate={onActivate} />);
    expect(Object.keys(DISPATCHER_GRAPH_NODE_TYPES)).toEqual(['stage', 'zone', 'agent']);
    const stage = screen.getByTestId('rf__node-prepare-diffs');
    stage.focus();
    fireEvent.keyDown(stage, { key: 'Enter' });
    expect(onActivate).toHaveBeenCalledWith('prepare-diffs', 'prepare-diffs');
    expect(document.querySelectorAll('.react-flow__handle').length).toBeGreaterThan(0);
    expect([...document.querySelectorAll('.react-flow__handle')].every((handle) => handle.getAttribute('aria-hidden') === 'true')).toBe(true);
  });

  test('активирует агентский слот пробелом и выводит данные проекции только как текст', () => {
    cleanup();
    const onActivate = vi.fn();
    const model = structuredClone(STATIC_COMPLETE_DISPATCHER_GRAPH);
    const agent = model.nodes.find((node) => node.type === 'agent')!;
    agent.data.invocations![0].workUnitId = '<img src=x onerror=alert(1)>';
    render(<DispatcherGraph model={model} onActivate={onActivate} />);
    const element = screen.getByTestId(`rf__node-${agent.id}`);
    element.focus();
    fireEvent.keyDown(element, { key: ' ' });
    expect(onActivate).toHaveBeenCalledWith(agent.data.circuitId, agent.id);
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
  });

  test('не перехватывает Space у фокусируемого тела коллекции', () => {
    cleanup();
    const onActivate = vi.fn();
    render(<DispatcherGraph model={STATIC_COMPLETE_DISPATCHER_GRAPH} onActivate={onActivate} />);
    const collection = document.querySelector<HTMLElement>('[role="region"]')!;
    collection.focus();
    fireEvent.keyDown(collection, { key: ' ' });
    expect(onActivate).not.toHaveBeenCalled();
  });
});
