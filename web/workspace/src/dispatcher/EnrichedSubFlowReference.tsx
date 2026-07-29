import { Box, Stack, Typography } from '@mui/material';
import { MarkerType, type Edge, type Node } from '@xyflow/react';
import { EnrichedDispatcherGraph, type EnrichedNodeData } from './EnrichedDispatcherGraph';

const stage = (id: string, title: string, x: number, width: number, accent: string, background: string): Node<EnrichedNodeData> => ({
  id,
  type: 'stage',
  position: { x, y: 0 },
  style: { width, height: 680, background, border: `2px solid ${accent}44`, borderRadius: 14 },
  data: { kind: 'stage', title, accent },
  draggable: false,
  selectable: false,
});

const item = (
  id: string,
  parentId: string,
  title: string,
  x: number,
  y: number,
  width: number,
  height: number,
  accent: string,
  detail: string,
  state = 'Ожидает',
  active = false,
): Node<EnrichedNodeData> => ({
  id,
  parentId,
  extent: 'parent',
  type: 'spec',
  position: { x, y },
  style: { width, height },
  data: { kind: 'card', title, detail, state, accent, active },
  draggable: false,
});

export const ENRICHED_SUBFLOW_NODES: Node<EnrichedNodeData>[] = [
  stage('prepare', '1 Подготовка различий', 0, 300, '#1976d2', '#f4f8ff'),
  stage('analysis', '2 Анализ DIF', 325, 430, '#1976d2', '#f7faff'),
  stage('mrq', '3 Формирование MRQ', 780, 650, '#6d3be7', '#faf8ff'),
  stage('classify', '4 Формирование пакетов', 1455, 430, '#7b1fa2', '#fcf7ff'),
  stage('target', '5 Исследование целевой базы', 1910, 500, '#0097a7', '#f4fcfd'),

  item('sources', 'prepare', 'Источники', 20, 70, 260, 76, '#1976d2', 'Типовая · Рабочая · Новая типовая', 'Готово'),
  item('acquire', 'prepare', 'Получение исходников', 40, 175, 220, 68, '#1976d2', 'Скачано объектов: 77 079', 'Готово'),
  item('index', 'prepare', 'Индексация BSL', 40, 275, 220, 68, '#1976d2', 'Проиндексировано: 77 079', 'Готово'),
  item('diff', 'prepare', 'Построение различий', 40, 375, 220, 68, '#1976d2', 'Найдено различий: 1 342', 'Готово'),
  item('dif-queue', 'prepare', 'Очередь DIF', 20, 485, 260, 100, '#1976d2', 'DIF-001 · DIF-002 · DIF-003 · ещё 128', '131'),

  item('analysis-queue', 'analysis', 'Очередь DIF', 18, 90, 92, 470, '#1976d2', '131 DIF', 'В работе', true),
  item('analyzer-1', 'analysis', 'Анализатор-1', 138, 105, 165, 112, '#1976d2', 'Текущий: DIF-002 · прогресс 65%', 'Анализирует', true),
  item('analyzer-2', 'analysis', 'Анализатор-2', 138, 270, 165, 96, '#757575', 'Работа не назначена', 'Свободен'),
  item('analyzer-3', 'analysis', 'Анализатор-3', 138, 420, 165, 112, '#ed6c02', 'Текущий: DIF-008 · повтор через 15 с', 'Ошибка'),
  item('semantic-dif', 'analysis', 'Смысловые DIF', 325, 120, 96, 190, '#1976d2', 'DIF-002 · DIF-005 · DIF-017', '23'),
  item('technical-noise', 'analysis', 'Технический шум', 325, 340, 96, 190, '#1976d2', 'DIF-008 · DIF-013', '2'),

  item('semantic-queue', 'mrq', 'Смысловые DIF', 18, 110, 92, 430, '#6d3be7', '23 DIF', 'Ожидает'),
  item('coordinator', 'mrq', 'Координатор MRQ', 140, 190, 150, 190, '#6d3be7', 'Опорный DIF: 002 · кандидатов: 4', 'Работает', true),
  item('grouper-1', 'mrq', 'Группировщик-1', 325, 80, 155, 110, '#6d3be7', 'DIF-002, 005, 017', 'В работе', true),
  item('grouper-2', 'mrq', 'Группировщик-2', 325, 235, 155, 110, '#6d3be7', 'DIF-007, DIF-129', 'В работе', true),
  item('grouper-3', 'mrq', 'Группировщик-3', 325, 390, 155, 110, '#6d3be7', 'Работа не назначена', 'Свободен'),
  item('proposal', 'mrq', 'Предложение группы', 515, 170, 116, 145, '#6d3be7', 'Заказ · DIF-002, 005, 017', 'Предложено'),
  item('review', 'mrq', 'Проверка', 515, 350, 116, 115, '#6d3be7', 'Доказательств: 6', '60%', true),
  item('publication', 'mrq', 'Публикация MRQ', 515, 490, 116, 100, '#6d3be7', 'MRQ-014 · MRQ-015', 'Готово: 13'),

  item('batch-input', 'classify', 'Исходные MRQ', 18, 105, 92, 450, '#7b1fa2', 'MRQ-014 · 015 · 016 · 017', '13'),
  item('classifier-1', 'classify', 'Классификатор', 140, 105, 170, 112, '#7b1fa2', 'Окно 1 · 13 MRQ', 'В работе', true),
  item('batch-validation', 'classify', 'Проверка покрытия', 140, 270, 170, 112, '#7b1fa2', 'Полное покрытие', 'Готово'),
  item('batch-output', 'classify', 'Пакеты', 330, 180, 82, 240, '#7b1fa2', 'MRQB-001 · MRQB-002', '2'),

  item('mrq-queue', 'target', 'Очередь MRQ', 18, 105, 92, 450, '#0097a7', 'MRQ-014 · 015 · 016 · 017', '11'),
  item('researcher-1', 'target', 'Исследователь-1', 140, 90, 170, 112, '#0097a7', 'MRQ-014 · прогресс 45%', 'В работе', true),
  item('researcher-2', 'target', 'Исследователь-2', 140, 270, 170, 112, '#ed6c02', 'MRQ-015 · разрыв · 70%', 'Разрыв', true),
  item('researcher-3', 'target', 'Исследователь-3', 140, 450, 170, 96, '#0097a7', 'Работа не назначена', 'Свободен'),
  item('target-db', 'target', 'Целевая база', 350, 180, 132, 170, '#0097a7', 'Версия 3.0.4 · объектов 28 731', 'Исследуется', true),
  item('results', 'target', 'Результаты', 330, 435, 152, 105, '#2e7d32', '7 типовых · 5 разрывов', '12 решений'),
];

const linked = (
  source: string,
  target: string,
  color: string,
  sourceHandle = 'right',
  targetHandle = 'left',
  animated = false,
): Edge => ({
  id: `${source}-${target}`,
  source,
  target,
  sourceHandle,
  targetHandle,
  animated,
  style: { stroke: color, strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
});

export const ENRICHED_SUBFLOW_EDGES: Edge[] = [
  linked('sources', 'acquire', '#1976d2', 'bottom', 'top'),
  linked('acquire', 'index', '#1976d2', 'bottom', 'top'),
  linked('index', 'diff', '#1976d2', 'bottom', 'top'),
  linked('diff', 'dif-queue', '#1976d2', 'bottom', 'top'),
  linked('dif-queue', 'analysis-queue', '#1976d2', 'right', 'left', true),
  linked('analysis-queue', 'analyzer-1', '#1976d2', 'right-top', 'left', true),
  linked('analysis-queue', 'analyzer-2', '#757575'),
  linked('analysis-queue', 'analyzer-3', '#ed6c02', 'right-bottom', 'left'),
  linked('analyzer-1', 'semantic-dif', '#1976d2', 'right-top', 'left-top', true),
  linked('analyzer-1', 'technical-noise', '#1976d2', 'right-bottom', 'left-top', true),
  linked('analyzer-2', 'semantic-dif', '#757575'),
  linked('analyzer-3', 'semantic-dif', '#ed6c02', 'right-bottom', 'left-bottom'),
  linked('semantic-dif', 'semantic-queue', '#6d3be7', 'right', 'left', true),
  linked('semantic-queue', 'coordinator', '#6d3be7', 'right', 'left', true),
  linked('coordinator', 'grouper-1', '#6d3be7', 'right-top', 'left', true),
  linked('coordinator', 'grouper-2', '#6d3be7', 'right', 'left', true),
  linked('coordinator', 'grouper-3', '#6d3be7', 'right-bottom', 'left'),
  linked('grouper-1', 'proposal', '#6d3be7', 'right-top', 'left-top', true),
  linked('grouper-2', 'proposal', '#6d3be7', 'right', 'left', true),
  linked('grouper-3', 'proposal', '#6d3be7', 'right-bottom', 'left-bottom'),
  linked('proposal', 'review', '#6d3be7', 'bottom', 'top', true),
  linked('review', 'publication', '#6d3be7', 'bottom', 'top', true),
  linked('publication', 'batch-input', '#7b1fa2', 'right', 'left', true),
  linked('batch-input', 'classifier-1', '#7b1fa2', 'right-top', 'left', true),
  linked('classifier-1', 'batch-validation', '#7b1fa2', 'bottom', 'top', true),
  linked('batch-validation', 'batch-output', '#7b1fa2', 'right', 'left', true),
  linked('batch-output', 'mrq-queue', '#0097a7', 'right', 'left', true),
  linked('mrq-queue', 'researcher-1', '#0097a7', 'right-top', 'left', true),
  linked('mrq-queue', 'researcher-2', '#ed6c02', 'right', 'left', true),
  linked('mrq-queue', 'researcher-3', '#0097a7', 'right-bottom', 'left'),
  linked('researcher-1', 'target-db', '#0097a7', 'right-top', 'left-top', true),
  linked('researcher-2', 'target-db', '#ed6c02', 'right', 'left', true),
  linked('researcher-3', 'target-db', '#0097a7', 'right-bottom', 'left-bottom'),
  linked('target-db', 'results', '#2e7d32', 'bottom', 'top', true),
];

export function EnrichedSubFlowReference({ title = 'Обогащённый эталон диспетчера' }: { title?: string }) {
  return <Stack spacing={1.2}>
    <Box>
      <Typography variant="h6" fontWeight={750}>{title}</Typography>
      <Typography variant="body2" color="text.secondary">Механика официального Sub Flow, дополненная этапами, агентами и состояниями исследования.</Typography>
    </Box>
    <Box data-testid="enriched-subflow-reference" sx={{ height: 720 }}>
      <EnrichedDispatcherGraph
        model={{ nodes: ENRICHED_SUBFLOW_NODES, edges: ENRICHED_SUBFLOW_EDGES }}
        fitView
        testId="enriched-reference-graph"
      />
    </Box>
  </Stack>;
}
