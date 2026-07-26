import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { activeProjection, emptyProjection, errorProjection, saturatedProjection } from './dispatcher.fixtures';
import type { DispatcherProjection } from '../src/dispatcher/projection';

const FIXED_TIME = '2026-07-21T12:00:00.000Z';
const CHANGE_ASSETS = path.resolve(process.cwd(), '../../openspec/changes/add-mrq-batch-classification-stage/assets');
const LEGACY_ASSETS = path.resolve(process.cwd(), '../../openspec/changes/make-dispatcher-new-working-screen/assets');
const VISUAL_MANIFEST = JSON.parse(readFileSync(path.join(CHANGE_ASSETS, 'visual-acceptance-manifest.json'), 'utf8')) as {
  images: Array<{ file: string; sha256: string; approved: boolean }>;
};
const LEGACY_VISUAL_MANIFEST = JSON.parse(readFileSync(path.join(LEGACY_ASSETS, 'visual-acceptance-manifest.json'), 'utf8')) as typeof VISUAL_MANIFEST;
const REQUIRED_ZONES = [
  'sources', 'vendor-baseline', 'target-cf', 'next-vendor', 'sources-acquire', 'diffs-build', 'indexes-build', 'prepare-dif-window',
  'analyze-dif-window', 'analyze-workers', 'analyze-meaning', 'analyze-noise',
  'form-meaning', 'form-coordinator', 'form-groupers', 'form-proposals', 'form-review', 'form-barrier', 'form-publication', 'form-summary',
  'classify-input', 'classify-workers', 'classify-validation', 'classify-batches',
  'decide-mrq-queue', 'decide-target-base', 'decide-researchers', 'decide-approval', 'decide-outcomes', 'decide-summary',
] as const;
const NEW_REQUIRED_ZONES = REQUIRED_ZONES.filter((zone) =>
  !['vendor-baseline', 'target-cf', 'next-vendor'].includes(zone));
const REQUIRED_LINKS = [
  ['sources', 'sources-acquire'], ['sources-acquire', 'diffs-build'], ['diffs-build', 'indexes-build'], ['indexes-build', 'prepare-dif-window'],
  ['prepare-dif-window', 'analyze-dif-window'], ['analyze-dif-window', 'analyze-workers'], ['analyze-workers', 'analyze-meaning'], ['analyze-workers', 'analyze-noise'],
  ['analyze-meaning', 'form-meaning'],
  ['form-meaning', 'form-coordinator'], ['form-coordinator', 'form-groupers'], ['form-groupers', 'form-proposals'], ['form-proposals', 'form-review'], ['form-review', 'form-barrier'], ['form-barrier', 'form-publication'], ['form-publication', 'form-summary'],
  ['form-summary', 'classify-input'], ['classify-input', 'classify-workers'], ['classify-workers', 'classify-validation'], ['classify-validation', 'classify-batches'],
  ['classify-batches', 'decide-mrq-queue'], ['decide-mrq-queue', 'decide-researchers'], ['decide-researchers', 'decide-target-base'], ['decide-target-base', 'decide-approval'], ['decide-approval', 'decide-outcomes'], ['decide-outcomes', 'decide-summary'],
] as const;
const NEW_REQUIRED_LINKS = [
  ['sources', 'acquire'], ['acquire', 'index'], ['index', 'diff'], ['diff', 'dif-queue'],
  ['dif-queue', 'analysis-queue'], ['analysis-queue', 'analyzer-1'], ['analyzer-1', 'semantic-dif'],
  ['semantic-dif', 'semantic-queue'], ['semantic-queue', 'coordinator'], ['coordinator', 'grouper-1'],
  ['grouper-1', 'proposal'], ['proposal', 'review'], ['review', 'publication'],
  ['publication', 'batch-input'], ['batch-input', 'classifier-1'], ['classifier-1', 'batch-validation'], ['batch-validation', 'batch-output'],
  ['batch-output', 'mrq-queue'], ['mrq-queue', 'researcher-1'], ['researcher-1', 'target-db'], ['target-db', 'results'],
] as const;

async function openFixture(page: Page, initialProjection: DispatcherProjection = saturatedProjection) {
  let projection = initialProjection;
  let workflowRequests = 0;
  let failingWorkflowRequests = 0;
  const actionRequests: Array<{ url: string; method: string; body: unknown; idempotencyKey: string | null }> = [];
  await page.addInitScript(({ fixedTime }) => {
    const NativeDate = Date;
    class FixedDate extends NativeDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        super(...(args.length ? args : [fixedTime]));
      }
      static now() { return new NativeDate(fixedTime).valueOf() + Math.floor(performance.now()); }
    }
    Object.defineProperty(window, 'Date', { value: FixedDate });
    Object.assign(window, { __eventSourceCount: 0, __eventSourceCloseCount: 0, __fixtureEventSource: null });
    class FixtureEventSource extends EventTarget {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;
      readonly CONNECTING = 0;
      readonly OPEN = 1;
      readonly CLOSED = 2;
      readyState = 1;
      url: string;
      withCredentials = true;
      constructor(url: string | URL) {
        super();
        this.url = String(url);
        const fixtureWindow = window as typeof window & { __eventSourceCount: number; __fixtureEventSource: FixtureEventSource | null };
        fixtureWindow.__eventSourceCount += 1;
        fixtureWindow.__fixtureEventSource = this;
        queueMicrotask(() => this.dispatchEvent(new Event('open')));
      }
      close() {
        this.readyState = 2;
        (window as typeof window & { __eventSourceCloseCount: number }).__eventSourceCloseCount += 1;
      }
      onopen = null;
      onmessage = null;
      onerror = null;
    }
    Object.defineProperty(window, 'EventSource', { value: FixtureEventSource });
  }, { fixedTime: FIXED_TIME });
  await page.route('**/api/v1/projects', async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'fixture', name: 'example Research', root: process.env.E2E_REPO }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });
  await page.route('**/api/v1/projects/fixture/workflow', (route) => {
    workflowRequests += 1;
    if (failingWorkflowRequests > 0) {
      failingWorkflowRequests -= 1;
      return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'fresh snapshot unavailable' }) });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ schema_version: '1', state: 'ready', workflow_fingerprint: 'sha256:fixture', gates: [], dispatcher: projection }),
    });
  });
  await page.route('**/api/v1/projects/fixture/dispatcher/**', async (route) => {
    const request = route.request();
    actionRequests.push({
      url: request.url(),
      method: request.method(),
      body: request.postDataJSON(),
      idempotencyKey: request.headers()['idempotency-key'] ?? null,
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: 'discover-mrq', action: 'retry', outcome: { status: 'running', run_id: 'run-new', thread_id: 'thread-new', revision: projection.revision + 1, summary: {} } }),
    });
  });
  await page.route('**/api/v1/projects/fixture/events?*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ events: [], next_cursor: 0, resync_required: false }),
  }));
  await page.route('**/api/v1/projects/fixture/source-setup', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      profiles: [],
      infobases: { acquisition_profile: '', roles: {} },
      infobases_fingerprint: 'sha256:fixture',
      external_artifacts: { artifacts: [] },
      upload_draft_fingerprint: 'sha256:fixture',
      connection_profiles: {},
      active_source: {},
      active_diff: {},
    }),
  }));
  await page.route('**/api/v1/projects/fixture/indexes', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }),
  }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Открыть репозиторий' }).click();
  await page.getByRole('textbox', { name: 'Название' }).fill('example Research');
  await page.getByRole('textbox', { name: 'Путь к репозиторию' }).fill(process.env.E2E_REPO!);
  await page.getByRole('button', { name: 'Открыть', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Диспетчер исследования' })).toBeVisible();
  await expect(page.getByTestId('dispatcher-canvas')).toBeVisible();
  return {
    workflowRequests: () => workflowRequests,
    eventSources: () => page.evaluate(() => (window as typeof window & { __eventSourceCount: number }).__eventSourceCount),
    closedEventSources: () => page.evaluate(() => (window as typeof window & { __eventSourceCloseCount: number }).__eventSourceCloseCount),
    failNextWorkflow() { failingWorkflowRequests += 1; },
    setProjection(nextProjection: DispatcherProjection) { projection = nextProjection; },
    actionRequests,
    async publish(nextProjection: DispatcherProjection, sequence: number) {
      projection = nextProjection;
      await page.evaluate((nextSequence) => {
        const source = (window as typeof window & { __fixtureEventSource: EventTarget }).__fixtureEventSource;
        source.dispatchEvent(new MessageEvent('workflow', { data: JSON.stringify({ sequence: nextSequence, payload: { revision: nextSequence } }) }));
      }, sequence);
    },
    async publishBurst(nextProjection: DispatcherProjection, sequences: number[]) {
      projection = nextProjection;
      await page.evaluate((nextSequences) => {
        const source = (window as typeof window & { __fixtureEventSource: EventTarget }).__fixtureEventSource;
        for (const sequence of nextSequences) {
          source.dispatchEvent(new MessageEvent('workflow', { data: JSON.stringify({ sequence, payload: { revision: sequence } }) }));
        }
      }, sequences);
    },
    async requireResync() {
      await page.evaluate(() => {
        const source = (window as typeof window & { __fixtureEventSource: EventTarget }).__fixtureEventSource;
        source.dispatchEvent(new MessageEvent('resync'));
      });
    },
  };
}

async function assertDispatcherGeometry(page: Page, _allInViewport = false) {
  const canvas = page.getByTestId('dispatcher-canvas');
  await expect(canvas.locator('.react-flow__viewport')).toHaveAttribute('style', /scale\(0\.9\)/);
  for (const zone of REQUIRED_ZONES) {
    const locator = canvas.locator(`[data-zone="${zone}"]`);
    await expect(locator).toBeVisible();
  }
  for (const [source, target] of REQUIRED_LINKS) {
    await expect(canvas.getByTestId(`rf__edge-flow:${source}:${target}`)).toHaveCount(1);
  }
  expect(await canvas.locator('.react-flow__edge').count()).toBeGreaterThanOrEqual(REQUIRED_LINKS.length);
  await expect(canvas.locator('.react-flow__edge[tabindex]')).toHaveCount(0);
  await expect(canvas.locator('.react-flow__handle:not([aria-hidden="true"])')).toHaveCount(0);
  const geometry = await page.evaluate((ids) => {
    const dispatcher = document.querySelector<HTMLElement>('[data-testid="dispatcher-scroll"]')!;
    const rectangles = ids.map((id) => document.querySelector<HTMLElement>(`[data-zone="${id}"]`)!.getBoundingClientRect());
    const nodes = [...document.querySelectorAll<HTMLElement>('.react-flow__node')];
    const overlap = nodes.some((node) => {
      const zones = [...node.querySelectorAll<HTMLElement>('[data-zone]')]
        .filter((zone) => !zone.parentElement?.closest('[data-zone]'))
        .map((zone) => zone.getBoundingClientRect());
      return zones.some((left, index) => zones.slice(index + 1).some((right) =>
        Math.min(left.right, right.right) - Math.max(left.left, right.left) > 1
        && Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top) > 1));
    });
    return {
      positiveZones: rectangles.every(({ width, height }) => width > 0 && height > 0),
      documentOverflow: document.documentElement.scrollWidth > window.innerWidth,
      dispatcher: { clientWidth: dispatcher.clientWidth, scrollWidth: dispatcher.scrollWidth },
      eventSources: (window as typeof window & { __eventSourceCount: number }).__eventSourceCount,
      overlap,
    };
  }, [...REQUIRED_ZONES]);
  expect(geometry.positiveZones).toBe(true);
  expect(geometry.documentOverflow).toBe(false);
  expect(geometry.eventSources).toBe(1);
  expect(geometry.overlap).toBe(false);
  return geometry;
}

async function assertDispatcherNewGeometry(page: Page, _allInViewport = false) {
  const canvas = page.getByTestId('dispatcher-new-canvas');
  for (const zone of NEW_REQUIRED_ZONES) {
    const locator = canvas.locator(`[data-zone="${zone}"]`).first();
    await expect(locator).toBeVisible();
  }
  for (const [source, target] of NEW_REQUIRED_LINKS) {
    await expect(canvas.getByTestId(`rf__edge-${source}-${target}`)).toHaveCount(1);
  }
  await expect(canvas.locator('.react-flow__edge[tabindex]')).toHaveCount(0);
  await expect(canvas.locator('.react-flow__handle:not([aria-hidden="true"])')).toHaveCount(0);
  const geometry = await page.evaluate((ids) => {
    const scroll = document.querySelector<HTMLElement>('[data-testid="dispatcher-new-scroll"]')!;
    const rectangles = ids.map((id) => document.querySelector<HTMLElement>(`[data-testid="dispatcher-new-canvas"] [data-zone="${id}"]`)!.getBoundingClientRect());
    return {
      positiveZones: rectangles.every(({ width, height }) => width > 0 && height > 0),
      documentOverflow: document.documentElement.scrollWidth > window.innerWidth,
      scroll: { clientWidth: scroll.clientWidth, scrollWidth: scroll.scrollWidth },
    };
  }, NEW_REQUIRED_ZONES);
  expect(geometry.positiveZones).toBe(true);
  expect(geometry.documentOverflow).toBe(false);
  return geometry;
}

function assertApprovedAsset(approvedName: string, legacy = false) {
  const root = legacy ? LEGACY_ASSETS : CHANGE_ASSETS;
  const manifest = legacy ? LEGACY_VISUAL_MANIFEST : VISUAL_MANIFEST;
  expect(existsSync(path.join(root, approvedName)), `Отсутствует утверждённый снимок ${approvedName}`).toBe(true);
  const image = manifest.images.find(({ file }) => file === approvedName);
  expect(image?.approved, `${approvedName} не утверждён в манифесте`).toBe(true);
  expect(createHash('sha256').update(readFileSync(path.join(root, approvedName))).digest('hex')).toBe(image?.sha256);
}

async function assertApprovedOrStructuralCandidate(page: Page, approvedName: string) {
  assertApprovedAsset(approvedName);
  await expect(page).toHaveScreenshot(approvedName, { animations: 'disabled', maxDiffPixelRatio: 0.001 });
}

test('single enriched dispatcher preserves shell, stream, panel, actions and focus', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const ledger = await openFixture(page, errorProjection);
  await assertDispatcherGeometry(page, true);
  await expect(page.getByRole('button', { name: 'Диспетчер', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /Текущий диспетчер|Новый диспетчер/ })).toHaveCount(0);

  const node = page.locator('.react-flow__node[data-id="analyze-dif"]');
  await node.click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toHaveCount(1);
  await page.getByRole('button', { name: 'Явный повтор' }).click();
  await page.getByRole('button', { name: 'Запустить повтор' }).click();
  await expect.poll(() => ledger.actionRequests.length).toBe(1);
  expect(ledger.actionRequests[0]).toMatchObject({ method: 'POST', body: { expected_workflow_fingerprint: 'sha256:fixture' } });

  await page.getByRole('button', { name: 'Закрыть' }).click();
  await expect(node).toBeFocused();
  await node.press('Enter');
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const viewport = page.getByTestId('dispatcher-canvas').locator('.react-flow__viewport');
  const initialTransform = await viewport.getAttribute('style');
  await page.getByTestId('dispatcher-canvas').locator('.react-flow__controls-zoomin').click();
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(initialTransform);
  const changedTransform = await viewport.getAttribute('style');

  const next = structuredClone(errorProjection);
  next.revision += 1;
  await ledger.publish(next, 1);
  await expect(page.getByText(`Ревизия ${next.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(viewport).toHaveAttribute('style', changedTransform!);
  expect(ledger.workflowRequests()).toBe(2);
  expect(await page.evaluate(() => (window as typeof window & { __eventSourceCount: number }).__eventSourceCount)).toBe(1);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toHaveCount(0);
  await expect(node).toBeFocused();
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test('empty current dispatcher keeps the complete structural matrix at 1920x1080', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openFixture(page, emptyProjection);
  await assertDispatcherGeometry(page, true);
  await expect(page.getByText('Легенда', { exact: true })).toBeVisible();
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-current-five-stage-empty-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-current-five-stage-empty-1920x1080.png');
});

test('focus falls back to the stage button when an agent disappears after SSE', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.locator('.react-flow__node[data-id="agent:analyze-dif:analyzer:analyzer-1"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const next = structuredClone(saturatedProjection);
  next.revision += 1;
  const role = next.agent_phases![0].roles[0];
  role.invocations = [];
  role.invocation_total = 0;
  role.invocation_omitted = 0;
  role.requested = role.running = role.queued = role.completed = role.failed = role.cancelled = role.interrupted = 0;
  await ledger.publish(next, 1);
  await expect(page.locator('.react-flow__node[data-id^="agent:analyze-dif:"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Закрыть' }).click();
  await expect(page.getByRole('button', { name: 'Анализ DIF', exact: true })).toBeFocused();
});

test('local graph failure keeps the real shell and open panel available', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.locator('.react-flow__node[data-id="analyze-dif"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const broken = structuredClone(saturatedProjection);
  broken.revision += 1;
  (broken.agent_phases![0] as unknown as { roles: unknown }).roles = {};
  await ledger.publish(broken, 1);
  await expect(page.getByText(/Холст диспетчера недоступен/)).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Диспетчер исследования' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Этапы диспетчера' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByText('Легенда', { exact: true })).toBeVisible();
});

test('saturated current dispatcher remains bounded and factual at 1920x1080', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openFixture(page);
  await assertDispatcherGeometry(page, true);
  await expect(page.locator('[data-zone="prepare-dif-window"]')).toContainText('Показано 4 из доступного окна');
  await expect(page.locator('[data-zone="analyze-dif-window"]')).toContainText('Показано 4 из 12');
  await expect(page.locator('[data-zone="classify-input"]')).toContainText('Показано 4 из 6');
  await expect(page.locator('[data-zone="classify-batches"]')).toContainText('Показано 4 из 5');
  await expect(page.locator('[data-zone="analyze-workers"]')).toContainText('Не показано вызовов: 2');
  await expect(page.locator('[data-invocation-id]')).toHaveCount(24);
  for (const invocation of ['invocation-01', 'coordinator-1', 'grouper-1', 'classifier-1', 'researcher-1']) {
    await expect(page.locator(`[data-invocation-id="${invocation}"]`)).toBeVisible();
  }
  await expect(page.getByLabel(/Вызов invocation-01: слот analyzer-1, единица DIF-00001, состояние Выполняется/)).toBeVisible();
  await expect(page.locator('[data-zone="classify-input"]')).toContainText('MRQ-00001 · DIF: 1 · доказательств: 2');
  await expect(page.getByRole('region', { name: 'Исходные MRQ: коллекция' })).toHaveAttribute('tabindex', '0');
  await expect(page.locator('[data-zone="decide-outcomes"]')).toContainText('MRQ-00001 · Принять типовое');
  await expect(page.locator('[data-zone="decide-summary"]')).toContainText('Вне объёма: 1');
  await expect(page.getByText('Легенда', { exact: true })).toBeVisible();
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-current-five-stage-saturated-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-current-five-stage-saturated-1920x1080.png');
});

test('saturated dispatcher uses local horizontal overview at 1280x720', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openFixture(page);
  const geometry = await assertDispatcherGeometry(page);
  expect(geometry.dispatcher.scrollWidth).toBeGreaterThan(geometry.dispatcher.clientWidth);
  const scroll = page.getByTestId('dispatcher-scroll');
  const before = await page.locator('[data-zone="decide-summary"]').evaluate((element) => element.getBoundingClientRect().left);
  await scroll.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  await expect.poll(() => page.locator('[data-zone="decide-summary"]').evaluate((element) => element.getBoundingClientRect().left)).toBeLessThan(before);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-current-five-stage-saturated-1280x720-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-current-five-stage-saturated-1280x720.png');
});

test('sixteen distinct slots remain visible inside their agent collection', async ({ page }) => {
  const dense = structuredClone(saturatedProjection);
  dense.agent_phases![0].roles[0].invocations.forEach((invocation, index) => {
    invocation.slot_id = `dense-${index + 1}`;
  });
  await openFixture(page, dense);
  await expect(page.locator('[data-agent-id^="agent:analyze-dif:"]')).toHaveCount(16);
  const geometry = await page.evaluate(() => {
    const parent = document.querySelector<HTMLElement>('[data-zone="analyze-workers"]')!.getBoundingClientRect();
    return [...document.querySelectorAll<HTMLElement>('[data-agent-id^="agent:analyze-dif:"]')].map((element) => {
      const rect = element.closest<HTMLElement>('.react-flow__node')!.getBoundingClientRect();
      return {
        inside: rect.left >= parent.left && rect.top >= parent.top && rect.right <= parent.right && rect.bottom <= parent.bottom,
        visible: rect.width > 0 && rect.height > 0,
      };
    });
  });
  expect(geometry.every((item) => item.inside && item.visible)).toBe(true);
});

test('motion follows the user preference and only confirmed activity animates', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await openFixture(page);
  await expect(page.locator('.react-flow__edge.animated')).not.toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toHaveCSS('animation-name', 'dispatcherNodePulse');
  await expect(page.locator('[data-zone="classify-batches"]')).toHaveCSS('animation-name', 'none');

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.react-flow__edge.animated')).toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toHaveCSS('animation-name', 'none');
});

test('dispatcher variants share one shell, stream and panel while restoring separate viewports', async ({ page }) => {
  const ledger = await openFixture(page);
  const currentCanvas = page.getByTestId('dispatcher-canvas');
  const currentViewport = currentCanvas.locator('.react-flow__viewport');
  await currentCanvas.locator('.react-flow__node[data-id="analyze-dif"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await currentCanvas.locator('.react-flow__controls-zoomin').click();
  await expect.poll(() => currentViewport.getAttribute('style')).not.toBe('transform: translate(4px, 10px) scale(0.9);');
  const currentTransform = await currentViewport.getAttribute('style');

  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await assertDispatcherNewGeometry(page, true);
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const newCanvas = page.getByTestId('dispatcher-new-canvas');
  const newViewport = newCanvas.locator('.react-flow__viewport');
  const newInitialTransform = await newViewport.getAttribute('style');
  await newCanvas.locator('.react-flow__controls-zoomin').click();
  await expect.poll(() => newViewport.getAttribute('style')).not.toBe(newInitialTransform);
  const newTransform = await newViewport.getAttribute('style');

  await page.getByRole('button', { name: 'Диспетчер', exact: true }).click();
  await expect(page.getByTestId('dispatcher-canvas').locator('.react-flow__viewport')).toHaveAttribute('style', currentTransform!);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await expect(page.getByTestId('dispatcher-new-canvas').locator('.react-flow__viewport')).toHaveAttribute('style', newTransform!);
  expect(ledger.workflowRequests()).toBe(1);
  expect(await ledger.eventSources()).toBe(1);

  await page.getByRole('button', { name: 'Закрыть' }).click();
  const newInitiator = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analyzer-1"]');
  await newInitiator.focus();
  await newInitiator.press('Enter');
  const refreshed = structuredClone(saturatedProjection);
  refreshed.revision += 2;
  await ledger.publishBurst(refreshed, Array.from({ length: 100 }, (_, index) => index + 1));
  await expect(page.getByText(`Ревизия ${refreshed.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByTestId('dispatcher-new-canvas').locator('.react-flow__viewport')).toHaveAttribute('style', newTransform!);
  await page.getByRole('button', { name: 'Закрыть' }).click();
  await expect(newInitiator).toBeFocused();
  await expect.poll(() => ledger.workflowRequests()).toBe(3);
  expect(await ledger.eventSources()).toBe(1);
});

test('dispatcher new keeps stale projection read-only until an explicit nondecreasing snapshot succeeds', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  const node = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analyzer-1"]');
  await node.click();
  const viewport = page.getByTestId('dispatcher-new-canvas').locator('.react-flow__viewport');
  const initialTransform = await viewport.getAttribute('style');
  await page.getByTestId('dispatcher-new-canvas').locator('.react-flow__controls-zoomin').click();
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(initialTransform);
  const changedTransform = await viewport.getAttribute('style');
  const oldRevision = saturatedProjection.revision;

  ledger.failNextWorkflow();
  await ledger.requireResync();
  await expect(page.getByText('fresh snapshot unavailable')).toBeVisible();
  await expect(page.getByText(`Ревизия ${oldRevision}`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Отменить задание' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Пересчитать с этапа' })).toBeDisabled();
  await expect(viewport).toHaveAttribute('style', changedTransform!);
  const failedCount = ledger.workflowRequests();
  await page.waitForTimeout(1100);
  expect(ledger.workflowRequests()).toBe(failedCount);
  expect(await ledger.eventSources()).toBe(1);

  ledger.failNextWorkflow();
  await page.getByRole('button', { name: 'Повторить снимок' }).click();
  await expect.poll(() => ledger.workflowRequests()).toBe(failedCount + 1);
  await expect(page.getByText('fresh snapshot unavailable')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Отменить задание' })).toBeDisabled();

  const fresh = structuredClone(saturatedProjection);
  fresh.revision += 1;
  ledger.setProjection(fresh);
  await page.getByRole('button', { name: 'Повторить снимок' }).click();
  await expect(page.getByText(`Ревизия ${fresh.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByText('fresh snapshot unavailable')).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(viewport).toHaveAttribute('style', changedTransform!);
  expect(await ledger.eventSources()).toBe(1);
});

test('active projection is distinct and only confirmed work is active on dispatcher new', async ({ page }) => {
  expect(activeProjection).not.toEqual(saturatedProjection);
  await openFixture(page, activeProjection);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await assertDispatcherNewGeometry(page, true);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(1);
  await expect(page.locator('[data-zone="analyze-workers"]')).toContainText('В работе');
  await expect(page.locator('[data-zone="form-coordinator"]')).toContainText('Ожидает');
});

test('both canvases render every classifier lifecycle state through the shared panel', async ({ page }) => {
  const ledger = await openFixture(page);
  const cases = [
    ['ready', undefined, 'ready', 'Готово', 'не захвачена'],
    ['active', 'running', 'active', 'В работе', 'fixture'],
    ['error', 'failed', 'error', 'Ошибка', 'Ошибка'],
    ['blocked', 'resumable', 'blocked', 'Ожидает', 'Остановлен'],
    ['complete', undefined, 'complete', 'Готово', 'не захвачена'],
    ['unknown', 'stale', 'unknown', 'Недоступно', 'Устарел'],
  ] as const;
  let sequence = 1;
  for (const [circuitState, leaseState, currentLabel, newLabel, leaseLabel] of cases) {
    const projection = structuredClone(saturatedProjection);
    projection.revision += sequence;
    projection.circuits.find(({ id }) => id === 'classify-mrq')!.state = circuitState;
    delete projection.jobs['classify-mrq'];
    projection.circuits.find(({ id }) => id === 'classify-mrq')!.leases = [];
    if (leaseState) {
      const lease = {
        job_id: 'classify-mrq' as const,
        thread_id: `classifier-${leaseState}`,
        work_unit_id: 'classify:sha256:fixture',
        owner: 'fixture',
        acquired_at: FIXED_TIME,
        renewed_at: FIXED_TIME,
        state: leaseState,
        summary: { run_id: `run-${leaseState}`, execution_snapshot_fingerprint: 'sha256:fixture' },
      };
      projection.jobs['classify-mrq'] = lease;
      projection.circuits.find(({ id }) => id === 'classify-mrq')!.leases = [lease];
    }
    await ledger.publish(projection, sequence);
    sequence += 1;
    for (const variant of ['Диспетчер', 'Диспетчер new'] as const) {
      await page.getByRole('button', { name: variant, exact: true }).click();
      const stage = variant === 'Диспетчер'
        ? page.locator('[data-testid="dispatcher-canvas"] .react-flow__node[data-id="classify-mrq"]')
        : page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="classify"]');
      await expect(stage).toContainText(variant === 'Диспетчер' ? currentLabel : newLabel);
      await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Формирование пакетов' }).click();
      const panel = page.getByRole('region', { name: 'Текущее задание' });
      await expect(panel).toBeVisible();
      await expect(panel).toContainText(leaseLabel);
      await expect(page.getByRole('heading', { name: 'Формирование пакетов' })).toBeVisible();
      await page.keyboard.press('Escape');
    }
  }
});

test('dispatcher new exposes factual error state and keyboard interaction without losing the shell', async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await openFixture(page, errorProjection);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await expect(page.locator('[data-zone="diffs-build"]')).toContainText('Ошибка');
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).focus();
  for (let index = 0; index < 40 && await page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analysis"]:focus').count() === 0; index += 1) {
    await page.keyboard.press('Tab');
  }
  const node = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analysis"]:focus');
  await expect(node).toHaveCount(1);
  await expect(node).toHaveAccessibleName(/.+/);
  await node.press('Enter');
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(node).toBeFocused();
  await expect(page.getByRole('heading', { name: 'Диспетчер исследования' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Журнал', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Реестры', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('region', { name: /вложенные зоны/ }).first()).toHaveAttribute('tabindex', '0');
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test('dispatcher new empty projection is structural before visual approval', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openFixture(page, emptyProjection);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await assertDispatcherNewGeometry(page, true);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(0);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-empty-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-new-five-stage-empty-1920x1080.png');
});

test('dispatcher new saturated projection is factual, bounded and candidate-ready at 1920x1080', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openFixture(page);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await assertDispatcherNewGeometry(page, true);
  await expect(page.locator('[data-zone="analyze-workers"], [data-zone="form-coordinator"], [data-zone="form-groupers"], [data-zone="classify-workers"], [data-zone="decide-researchers"]')).toHaveCount(5);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(24);
  await expect(page.locator('[data-zone="decide-target-base"]')).toContainText('Версия и размер не подтверждены');
  await expect(page.locator('[data-zone="classify-input"]')).toContainText('MRQ-00001 · MRQ-00002');
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-saturated-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-new-five-stage-saturated-1920x1080.png');
});

test('dispatcher new remains reachable through local overview at 1280x720', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await openFixture(page);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  const geometry = await assertDispatcherNewGeometry(page);
  expect(geometry.scroll.scrollWidth).toBeGreaterThan(geometry.scroll.clientWidth);
  const scroll = page.getByTestId('dispatcher-new-scroll');
  const before = await page.locator('[data-zone="decide-summary"]').evaluate((element) => element.getBoundingClientRect().left);
  await scroll.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
  await expect.poll(() => page.locator('[data-zone="decide-summary"]').evaluate((element) => element.getBoundingClientRect().left)).toBeLessThan(before);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-saturated-1280x720-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-new-five-stage-saturated-1280x720.png');
});

test('dispatcher new agent focus falls back after SSE removal and its local failure keeps the shell', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await page.locator('[data-invocation-id="invocation-01"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const next = structuredClone(saturatedProjection);
  next.revision += 1;
  const role = next.agent_phases![0].roles[0];
  role.invocations = [];
  role.invocation_total = role.invocation_omitted = 0;
  role.requested = role.running = role.queued = role.completed = role.failed = role.cancelled = role.interrupted = 0;
  await ledger.publish(next, 1);
  await expect(page.locator('[data-invocation-id="invocation-01"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Закрыть' }).click();
  await expect(page.getByRole('button', { name: 'Анализ DIF', exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Анализ DIF', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();

  const broken = structuredClone(next);
  broken.revision += 1;
  (broken.items as unknown as { mrqs: unknown }).mrqs = null;
  await ledger.publish(broken, 2);
  await expect(page.getByText(/Холст «Диспетчер new» недоступен/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Этапы диспетчера' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Журнал', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Реестры', exact: true }).first()).toBeVisible();
  await expect(page.getByText('Легенда', { exact: true })).toBeVisible();
  await expect(page.getByTestId('dispatcher-canvas')).toHaveCount(0);
});

test('return from a non-working view requires a fresh snapshot and fails closed', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.getByRole('button', { name: 'Источники', exact: true }).first().click();
  await expect(page.getByTestId('dispatcher-canvas')).toHaveCount(0);
  await expect.poll(() => ledger.closedEventSources()).toBe(1);

  ledger.failNextWorkflow();
  await page.getByRole('button', { name: 'Диспетчер', exact: true }).click();
  await expect(page.getByText('fresh snapshot unavailable')).toBeVisible();
  await expect(page.getByTestId('dispatcher-canvas')).toHaveCount(0);
  expect(await ledger.eventSources()).toBe(1);

  const fresh = structuredClone(saturatedProjection);
  fresh.revision += 10;
  ledger.setProjection(fresh);
  await page.getByRole('button', { name: 'Повторить снимок' }).click();
  await expect(page.getByText(`Ревизия ${fresh.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByTestId('dispatcher-canvas')).toBeVisible();
  expect(ledger.workflowRequests()).toBe(3);
  expect(await ledger.eventSources()).toBe(2);
});

test('both dispatcher canvases use contextual and compact navigation with a fresh return lifecycle', async ({ page }) => {
  const ledger = await openFixture(page);
  let expectedStreams = 1;
  const returnTo = async (variant: 'Диспетчер' | 'Диспетчер new') => {
    await page.getByRole('button', { name: variant, exact: true }).click();
    expectedStreams += 1;
    await expect.poll(() => ledger.eventSources()).toBe(expectedStreams);
    await expect.poll(() => ledger.closedEventSources()).toBe(expectedStreams - 1);
  };
  for (const variant of ['Диспетчер', 'Диспетчер new'] as const) {
    await page.getByRole('button', { name: variant, exact: true }).click();

    await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Подготовка различий' }).click();
    await page.getByRole('button', { name: 'Настроить источники' }).click();
    await expect(page.getByRole('heading', { name: 'Источники' })).toBeVisible();
    await returnTo(variant);

    await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Подготовка различий' }).click();
    await page.getByRole('button', { name: 'Проверить индексы' }).click();
    await expect(page.getByText(/Индексы — одноразовое ускорение/)).toBeVisible();
    await returnTo(variant);

    await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Анализ DIF' }).click();
    await page.getByRole('button', { name: 'Профили и параметры' }).click();
    await expect(page.getByText('Следующее типизированное действие')).toBeVisible();
    await returnTo(variant);

    await page.getByRole('button', { name: 'Журнал', exact: true }).first().click();
    await expect(page.getByRole('textbox', { name: 'Поиск в событиях и журналах' })).toBeVisible();
    await returnTo(variant);

    await page.getByRole('button', { name: 'Реестры', exact: true }).first().click();
    await expect(page.getByLabel('Реестр')).toBeVisible();
    await returnTo(variant);
  }
  expect(ledger.workflowRequests()).toBe(11);
});

test('original enriched reference remains available as a separate screen', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await openFixture(page);
  await page.getByRole('button', { name: 'Обогащённая схема' }).click();
  await expect(page.getByTestId('enriched-subflow-reference')).toBeVisible();
  await expect(page.getByText('Обогащённый эталон диспетчера')).toBeVisible();
  assertApprovedAsset('enriched-reference-baseline-1920x1080.png', true);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-five-stage-reference-saturated-1920x1080-candidate.png'), animations: 'disabled' });
  } else {
    await assertApprovedOrStructuralCandidate(page, 'dispatcher-five-stage-reference-saturated-1920x1080.png');
  }
  await page.getByRole('button', { name: 'Диспетчер new' }).click();
  await expect(page.getByTestId('dispatcher-new-canvas')).toBeVisible();
  await expect(page.getByTestId('enriched-subflow-reference')).toHaveCount(0);
});

test('enriched reference preserves screen links and opens dispatcher new directly', async ({ page }) => {
  await openFixture(page);
  const navigation = page.locator('header button');
  const navigationLabels = await navigation.allTextContents();
  const headerBox = await page.locator('header').boundingBox();

  await page.getByRole('button', { name: 'Обогащённая схема', exact: true }).click();

  await expect(page.getByTestId('enriched-subflow-reference')).toBeVisible();
  await expect(navigation).toHaveText(navigationLabels);
  expect(await page.locator('header').boundingBox()).toEqual(headerBox);
  await page.getByRole('button', { name: 'Диспетчер new', exact: true }).click();
  await expect(page.getByTestId('dispatcher-new-canvas')).toBeVisible();
});
