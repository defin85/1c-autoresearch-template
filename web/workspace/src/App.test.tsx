import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { Home, StagePage } from './App';

vi.stubGlobal('EventSource', class { addEventListener() {} close() {} });
vi.stubGlobal('fetch', vi.fn(async (input: string) => ({
  ok: true, status: 200, json: async () => input.includes('/workflow') ? { project: { id: 'p', name: 'P', root: '/p', setup: {} }, generated_at: '', stages: [{ id: 'research', title: '<img onerror=alert(1)>', kind: 'agent', dependencies: [], status: 'pending', ready: true, blockers: [] }] } : [],
})));

describe('stage screen', () => {
  it('renders untrusted text literally and preserves unsaved prompt across refresh render', async () => {
    render(<MemoryRouter initialEntries={['/projects/p/stages/research']}><Routes><Route path="/projects/:projectId/stages/:stageId" element={<StagePage />} /></Routes></MemoryRouter>);
    expect(await screen.findByText('<img onerror=alert(1)>')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Дополнение пользователя'), { target: { value: 'мой незаписанный текст' } });
    expect(screen.getByDisplayValue('мой незаписанный текст')).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
  });
});

describe('project folder picker', () => {
  it('opens a directory and returns to its parent', async () => {
    vi.mocked(fetch).mockImplementation(async input => {
      const url = String(input);
      const value = url.includes('path=%2Froot%2Fchild')
        ? { current: '/root/child', parent: '/root', directories: [] }
        : url.includes('/filesystem/directories')
          ? { current: '/root', parent: null, directories: [{ name: 'child', path: '/root/child' }] }
          : [];
      return { ok: true, status: 200, json: async () => value } as Response;
    });
    render(<MemoryRouter><Home /></MemoryRouter>);
    fireEvent.click(await screen.findByRole('button', { name: 'Создать или открыть проект' }));
    fireEvent.click(screen.getByRole('button', { name: 'Выбрать' }));
    fireEvent.click(await screen.findByRole('button', { name: /child/ }));
    expect(await screen.findByText('/root/child')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '← Назад' }));
    expect(await screen.findByText('/root')).toBeInTheDocument();
  });
});
