import { expect, test, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { activeProjection, approvalProjection, emptyProjection, errorProjection, saturatedProjection } from './dispatcher.fixtures';
import type { DispatcherProjection } from '../src/dispatcher/projection';

const FIXED_TIME = '2026-07-21T12:00:00.000Z';
const CHANGE_ASSETS = path.resolve(process.cwd(), 'e2e/visual-assets/current');
const LEGACY_ASSETS = path.resolve(process.cwd(), 'e2e/visual-assets/legacy');
const VISUAL_MANIFEST = JSON.parse(readFileSync(path.join(CHANGE_ASSETS, 'visual-acceptance-manifest.json'), 'utf8')) as {
  images: Array<{ file: string; sha256: string; approved: boolean }>;
};
const LEGACY_VISUAL_MANIFEST = JSON.parse(readFileSync(path.join(LEGACY_ASSETS, 'visual-acceptance-manifest.json'), 'utf8')) as typeof VISUAL_MANIFEST;
const REQUIRED_ZONES = [
  'sources', 'vendor-baseline', 'target-cf', 'next-vendor', 'sources-acquire', 'diffs-build', 'indexes-build', 'prepare-dif-window',
  'analyze-dif-window', 'analyze-workers', 'analyze-meaning', 'analyze-noise',
  'form-meaning', 'form-coordinator', 'form-groupers', 'form-proposals', 'form-review', 'form-publication',
  'classify-input', 'classify-workers', 'classify-validation', 'classify-batches',
  'decide-mrq-queue', 'decide-target-base', 'decide-researchers', 'decide-summary',
] as const;
const NEW_REQUIRED_ZONES = REQUIRED_ZONES.filter((zone) =>
  !['vendor-baseline', 'target-cf', 'next-vendor'].includes(zone));
const NEW_REQUIRED_LINKS = [
  ['sources', 'acquire'], ['acquire', 'index'], ['index', 'diff'], ['diff', 'dif-queue'],
  ['dif-queue', 'analysis-queue'], ['analysis-queue', 'analyzer-1'], ['analyzer-1', 'semantic-dif'],
  ['semantic-dif', 'semantic-queue'], ['semantic-queue', 'coordinator'], ['coordinator', 'grouper-1'],
  ['grouper-1', 'proposal'], ['proposal', 'review'], ['review', 'publication'],
  ['publication', 'batch-input'], ['batch-input', 'classifier-1'], ['classifier-1', 'batch-validation'], ['batch-validation', 'batch-output'],
  ['batch-output', 'mrq-queue'], ['mrq-queue', 'researcher-1'], ['researcher-1', 'target-db'], ['target-db', 'results'],
] as const;
const INDEX_CAPABILITIES = [
  'code-search-lexical', 'code-search-hybrid', 'symbol-info', 'symbol-info-positional',
  'graph-overview', 'graph-schema', 'graph-resolve', 'graph-node', 'graph-source',
  'graph-neighbors', 'graph-callers', 'graph-callees', 'metadata-info', 'metadata-tree',
  'metadata-object', 'metadata-form', 'diagnostics-catalog', 'diagnostics-schema',
  'diagnostics-file', 'diagnostics-workspace', 'reference-docs-find',
  'reference-docs-search', 'reference-syntax-help', 'reference-its-help',
] as const;
const schema3IndexConfiguration = (backends: Array<{ adapter_id: string; engine_version: string }>) => ({
  schema_version: '3',
  machine_contract_version: '1.3',
  backends,
  routes: Object.fromEntries(INDEX_CAPABILITIES.map((capability) => [capability, backends.map(({ adapter_id }) => adapter_id)])),
  service_profiles: { lexical: 'source-search-lexical/v2', hybrid: 'source-search-hybrid/v2' },
});

async function openFixture(
  page: Page,
  initialProjection: DispatcherProjection = saturatedProjection,
  sourceSetup?: Record<string, unknown>,
  indexSetup?: Record<string, unknown>,
) {
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
      body: JSON.stringify({
        schema_version: '1',
        state: 'ready',
        workflow_fingerprint: 'sha256:fixture',
        gates: [],
        extension_scope_blockers: (
          ((sourceSetup?.extension_scope as { unreviewed?: string[] } | undefined)?.unreviewed) || []
        ).map((uuid) => ({ code: 'extension_scope_required', message: 'review required', action: 'review_extension_scope', uuid })),
        dispatcher: projection,
      }),
    });
  });
  await page.route('**/api/v1/projects/fixture/dispatcher/**', async (route) => {
    const request = route.request();
    if (request.method() === 'GET' && request.url().includes('/dispatcher/inspect?')) {
      const url = new URL(request.url());
      const invocationId = url.searchParams.get('invocation_id');
      const historyCursor = url.searchParams.get('history_cursor');
      const eventCursor = url.searchParams.get('event_cursor');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          invocation: invocationId ? { invocation_id: invocationId, status: 'running', error_summary: '<b>literal</b>' } : undefined,
          slot: invocationId ? undefined : { slot_id: url.searchParams.get('slot_id'), state: 'idle', idle_reason_code: 'work_not_requested' },
          history: {
            items: [{ invocation_id: invocationId, marker: historyCursor ? 'history-page-2' : 'history-page-1' }],
            available: true,
            next_cursor: historyCursor ? undefined : 'history-next',
          },
          events: {
            items: [{ sequence: eventCursor ? 2 : 1, marker: eventCursor ? 'event-page-2' : 'event-page-1' }],
            available: true,
            next_cursor: eventCursor ? undefined : 'event-next',
          },
          source_search: invocationId ? {
            available: true,
            operations: ['code.search_lexical'],
            scope: { component_count: 1, path_count: 2, fingerprint: 'sha256:scope' },
            configured_limits: { max_calls: 4, max_concurrent_calls: 1 },
            capacity_reserve_bytes: 4096,
            usage: { calls: 2, in_flight: 0, results: 3, returned_bytes: 512, backend_seconds: 1.5 },
            status_counts: { completed: 2 },
            route_summaries: [{ capability: 'code-search-lexical', selected_backend_id: 'rlm-tools-bsl', fallback_reason: 'index_not_ready', calls: 2 }],
            last_error: { code: 'source_search.budget_exhausted', limit: 'max_calls', limit_value: 4, consumed: 4, requested: 1, recovery: 'start_new_invocation' },
            ledger_complete: true,
            reconciled: true,
            ledger_fingerprint: 'sha256:ledger',
            items: [{ ordinal: 1, query_hmac: 'v1:opaque', status: 'completed' }],
            next_cursor: undefined,
          } : { available: false, items: [], next_cursor: undefined },
        }),
      });
      return;
    }
    actionRequests.push({
      url: request.url(),
      method: request.method(),
      body: request.postDataJSON(),
      idempotencyKey: request.headers()['idempotency-key'] ?? null,
    });
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: 'analyze-dif', action: 'retry', outcome: { status: 'running', run_id: 'run-new', thread_id: 'thread-new', revision: projection.revision + 1, summary: {} } }),
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
    body: JSON.stringify(sourceSetup || {
      profiles: [],
      infobases: { acquisition_profile: '', roles: {} },
      infobases_fingerprint: 'sha256:fixture',
      extension_scope: { extensions: [], included: [], excluded: [], dormant: [], unreviewed: [] },
      external_artifacts: { artifacts: [] },
      upload_draft_fingerprint: 'sha256:fixture',
      connection_profiles: {},
      active_source: {},
      active_diff: {},
    }),
  }));
  await page.route('**/api/v1/projects/fixture/indexes', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(indexSetup || {
      items: [],
      configuration: schema3IndexConfiguration([
        { adapter_id: 'rlm-tools-bsl', engine_version: '1.30.0' },
      ]),
      configuration_fingerprint: 'sha256:index-config',
    }),
  }));
  await page.route('**/api/v1/projects/fixture/search-services', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      schema_version: 'search-services/v1',
      state_fingerprint: 'sha256:search-services',
      profiles: [],
    }),
  }));
  await page.route('**/api/v1/projects/fixture/indexes/configuration-preview', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      plan_fingerprint: 'sha256:index-plan',
      degraded_routes: ['code-search-lexical'],
      rebuild_backends: [],
    }),
  }));
  await page.route('**/api/v1/projects/fixture/actions', async (route) => {
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
      body: JSON.stringify({ operation: 'indexes.validate', components: [] }),
    });
  });
  await page.route('**/api/v1/projects/fixture/registries/diff-inventory?*', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      diff_generation_id: 'generation-fixture',
      items: [{ stable_diff_id: 'DIF-00001', path: 'Catalogs/Fixture.xml', state: 'queued' }],
      has_more: false,
    }),
  }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Открыть репозиторий' }).click();
  await page.getByRole('textbox', { name: 'Название' }).fill('example Research');
  await page.getByRole('textbox', { name: 'Путь к репозиторию' }).fill(process.env.E2E_REPO!);
  await page.getByRole('button', { name: 'Открыть', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Диспетчер исследования' })).toBeVisible();
  await expect(page.getByTestId('dispatcher-new-canvas')).toBeVisible();
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
    async degradeStream() {
      await page.evaluate(() => {
        const source = (window as typeof window & { __fixtureEventSource: EventTarget }).__fixtureEventSource;
        source.dispatchEvent(new Event('error'));
      });
    },
    async recoverStream() {
      await page.evaluate(() => {
        const source = (window as typeof window & { __fixtureEventSource: EventTarget }).__fixtureEventSource;
        source.dispatchEvent(new Event('open'));
      });
    },
  };
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

async function assertNoClippedNodeContent(page: Page, testId = 'dispatcher-new-canvas') {
  const issues = await page.getByTestId(testId).evaluate((canvas) => {
    const tolerance = 2;
    const result: string[] = [];
    for (const container of canvas.querySelectorAll<HTMLElement>('[data-content-inset]')) {
      const style = getComputedStyle(container);
      const insets = [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].map(Number.parseFloat);
      if (insets.some((inset) => inset < 6)) {
        result.push(`${container.dataset.zone || container.dataset.agentSlotId}: внутренний отступ меньше 6 px`);
      }
    }
    for (const node of canvas.querySelectorAll<HTMLElement>('.react-flow__node:not(.react-flow__node-stage)')) {
      const body = node.firstElementChild as HTMLElement | null;
      if (!body) continue;
      for (const region of body.querySelectorAll<HTMLElement>('[role="region"]')) {
        if (region.scrollHeight > region.clientHeight + tolerance && !['auto', 'scroll'].includes(getComputedStyle(region).overflowY)) {
          result.push(`${node.dataset.id}: переполнение области недоступно`);
        }
      }
      const bounds = body.getBoundingClientRect();
      for (const text of body.querySelectorAll<HTMLElement>('p, .MuiChip-label')) {
        if (!text.textContent?.trim()) continue;
        const scrollRegion = text.closest<HTMLElement>('[role="region"]');
        if (scrollRegion && scrollRegion.scrollHeight > scrollRegion.clientHeight + tolerance) continue;
        const box = text.getBoundingClientRect();
        if (box.left < bounds.left - tolerance || box.right > bounds.right + tolerance
          || box.top < bounds.top - tolerance || box.bottom > bounds.bottom + tolerance) {
          result.push(`${node.dataset.id}: обрезан текст «${text.textContent.trim().slice(0, 40)}»`);
        }
      }
      const textElements = [...body.querySelectorAll<HTMLElement>('p, .MuiChip-label')]
        .filter((text) => text.textContent?.trim() && getComputedStyle(text).visibility !== 'hidden');
      for (let left = 0; left < textElements.length; left += 1) {
        const leftBox = textElements[left].getBoundingClientRect();
        for (let right = left + 1; right < textElements.length; right += 1) {
          const rightBox = textElements[right].getBoundingClientRect();
          const overlapWidth = Math.min(leftBox.right, rightBox.right) - Math.max(leftBox.left, rightBox.left);
          const overlapHeight = Math.min(leftBox.bottom, rightBox.bottom) - Math.max(leftBox.top, rightBox.top);
          if (overlapWidth > tolerance && overlapHeight > tolerance) {
            result.push(`${node.dataset.id}: «${textElements[left].textContent?.trim()}» накладывается на «${textElements[right].textContent?.trim()}»`);
          }
        }
      }
    }
    return [...new Set(result)];
  });
  expect(issues, issues.join('\n')).toEqual([]);
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
  if (process.env.PORTABLE_TEMPLATE === '1') return;
  const expected = readFileSync(path.join(CHANGE_ASSETS, approvedName)).toString('base64');
  const actual = (await page.screenshot({ animations: 'disabled' })).toString('base64');
  const headerBottom = Math.ceil(await page.locator('header').evaluate((header) => header.getBoundingClientRect().bottom));
  const comparison = await page.evaluate(async ({ expectedBase64, actualBase64, ignoredRows }) => {
    const decode = async (base64: string) => {
      const binary = atob(base64);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      return createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    };
    const [expectedImage, actualImage] = await Promise.all([decode(expectedBase64), decode(actualBase64)]);
    if (expectedImage.width !== actualImage.width || expectedImage.height !== actualImage.height) {
      return { sameSize: false, ratio: 1 };
    }
    const pixels = (image: ImageBitmap) => {
      const canvas = new OffscreenCanvas(image.width, image.height);
      const context = canvas.getContext('2d')!;
      context.drawImage(image, 0, 0);
      return context.getImageData(0, 0, image.width, image.height).data;
    };
    const expectedPixels = pixels(expectedImage);
    const actualPixels = pixels(actualImage);
    let changed = 0;
    const first = ignoredRows * expectedImage.width * 4;
    for (let offset = first; offset < expectedPixels.length; offset += 4) {
      if (
        expectedPixels[offset] !== actualPixels[offset]
        || expectedPixels[offset + 1] !== actualPixels[offset + 1]
        || expectedPixels[offset + 2] !== actualPixels[offset + 2]
        || expectedPixels[offset + 3] !== actualPixels[offset + 3]
      ) changed += 1;
    }
    return {
      sameSize: true,
      ratio: changed / ((expectedImage.height - ignoredRows) * expectedImage.width),
    };
  }, { expectedBase64: expected, actualBase64: actual, ignoredRows: headerBottom });
  expect(comparison.sameSize).toBe(true);
  expect(comparison.ratio).toBeLessThanOrEqual(0.001);
}

test('motion follows the user preference and only confirmed activity animates', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await openFixture(page);
  await expect(page.locator('.react-flow__edge.animated')).not.toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toHaveCSS('animation-name', 'enrichedRolePulse');
  await expect(page.locator('[data-zone="classify-batches"]')).toHaveCSS('animation-name', 'none');

  await page.emulateMedia({ reducedMotion: 'reduce' });
  await expect(page.locator('.react-flow__edge.animated')).toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toHaveCSS('animation-name', 'none');
});

test('the sole dispatcher keeps one shell, stream, panel and viewport', async ({ page }) => {
  const ledger = await openFixture(page);
  await expect(page.getByRole('button', { name: 'Диспетчер', exact: true })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Диспетчер new', exact: true })).toHaveCount(0);
  const canvas = page.getByTestId('dispatcher-new-canvas');
  const viewport = canvas.locator('.react-flow__viewport');
  await canvas.locator('.react-flow__node[data-id="analysis"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  const initialTransform = await viewport.getAttribute('style');
  await canvas.locator('.react-flow__controls-zoomin').click();
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(initialTransform);
  const transform = await viewport.getAttribute('style');
  expect(ledger.workflowRequests()).toBe(1);
  expect(await ledger.eventSources()).toBe(1);

  await page.getByRole('button', { name: 'Закрыть' }).click();
  const initiator = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analyzer-1"] [data-dispatcher-kind="role"]');
  await initiator.focus();
  await initiator.press('Enter');
  const refreshed = structuredClone(saturatedProjection);
  refreshed.revision += 2;
  await ledger.publishBurst(refreshed, Array.from({ length: 100 }, (_, index) => index + 1));
  await expect(page.getByText(`Ревизия ${refreshed.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(viewport).toHaveAttribute('style', transform!);
  await page.getByRole('button', { name: 'Закрыть' }).click();
  await expect(initiator).toBeFocused();
  await expect.poll(() => ledger.workflowRequests()).toBe(3);
  expect(await ledger.eventSources()).toBe(1);
});

test('the sole dispatcher sends one approved action with the workflow fingerprint', async ({ page }) => {
  const projection = structuredClone(approvalProjection);
  projection.items.proposals[0] = {
    ...projection.items.proposals[0],
    job_id: 'consolidate-mrq',
    kind: 'approval',
    approval_stage: 'consolidation',
  };
  const ledger = await openFixture(page, projection);
  projection.jobs = { 'consolidate-mrq': { ...projection.jobs['analyze-dif'], job_id: 'consolidate-mrq' } };
  await page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="mrq"]').click();
  await page.getByRole('button', { name: 'Одобрить предложение' }).click();
  await expect.poll(() => ledger.actionRequests.length).toBe(1);
  expect(ledger.actionRequests[0]).toMatchObject({
    method: 'POST',
    body: {
      actor: 'local-user',
      proposal_key: projection.items.proposals[0].id,
      expected_fingerprint: 'sha256:fixture',
    },
  });
  expect(ledger.actionRequests[0].url).toContain('/dispatcher/consolidate-mrq/approve-consolidation');
  expect(ledger.actionRequests[0].idempotencyKey).toBeTruthy();
});

test('context inspector navigates circuit, role, slot, invocation and queue item with exact details', async ({ page }) => {
  await openFixture(page);
  const analyzer = page.locator('.react-flow__node[data-id="analyzer-1"]');
  await analyzer.locator('[data-dispatcher-kind="role"]').click();
  await expect(page.getByRole('heading', { name: 'Роль analyzer' })).toBeFocused();
  await page.getByRole('complementary', { name: 'Сведения диспетчера' }).getByRole('button', { name: 'analyzer-1' }).click();
  await expect(page.getByRole('heading', { name: /Слот analyzer-1/ })).toBeFocused();
  await analyzer.locator('[data-dispatcher-kind="role"]').click();
  await page.getByRole('complementary', { name: 'Сведения диспетчера' }).getByRole('button', { name: /Вызов invocation-01/ }).click();
  await expect(page.getByRole('heading', { name: 'Вызов invocation-01' })).toBeFocused();
  await expect(page.getByText('<b>literal</b>')).toBeVisible();
  await expect(page.locator('b')).toHaveCount(0);
  await expect(page.getByText('Динамический поиск по исходникам')).toBeVisible();
  await expect(page.getByText('rlm-tools-bsl')).toBeVisible();
  await expect(page.getByText('index_not_ready')).toBeVisible();
  await expect(page.getByText('sha256:ledger')).toBeVisible();
  await expect(page.getByText('source_search.budget_exhausted')).toBeVisible();
  await expect(page.getByText('start_new_invocation')).toBeVisible();
  await page.getByRole('button', { name: 'Ещё история' }).click();
  await expect(page.getByText('history-page-2')).toBeVisible();
  await page.getByRole('button', { name: 'Ещё события' }).click();
  await expect(page.getByText('event-page-2')).toBeVisible();
  await page.getByRole('button', { name: 'Открыть в журнале' }).click();
  await expect(page.getByRole('textbox', { name: 'Поиск в событиях и журналах' })).toHaveValue('invocation-01');

  await page.getByRole('button', { name: 'Диспетчер', exact: true }).click();
  await page.locator('.react-flow__node[data-id="dif-queue"]').click();
  await expect(page.getByRole('heading', { name: 'Очередь', exact: true })).toBeVisible();
  await page.getByRole('complementary', { name: 'Сведения диспетчера' }).getByRole('button', { name: 'DIF-00001' }).click();
  await expect(page.getByRole('heading', { name: 'DIF DIF-00001' })).toBeFocused();
  await page.getByRole('button', { name: 'Открыть запись реестра' }).click();
  const record = page.getByLabel('Запись реестра DIF-00001');
  await expect(record).toBeVisible();
  await expect(record).toContainText('Catalogs/Fixture.xml');
});

test('index workspace exposes mixed backends, safe failure and reviewed validation', async ({ page }) => {
  const ledger = await openFixture(page, saturatedProjection, undefined, {
    items: [
      {
        component_id: 'target_cf:configuration',
        source_generation_id: 'gen',
        fingerprint: 'sha256:source',
        adapter_id: 'bsl-analyzer',
        adapter_version: 'bsl-analyzer-workspace/v1',
        engine_version: '0.2.63',
        bsl_file_count: 5,
        status: 'ready',
        last_validation: '2026-07-29T00:00:00Z',
        index_fingerprint: 'sha256:bsl',
        contract_version: '1.1',
        capabilities: ['code-search-lexical'],
        route_priorities: { 'code-search-lexical': 0 },
      },
      {
        component_id: 'target_cf:configuration',
        source_generation_id: 'gen',
        fingerprint: 'sha256:source',
        adapter_id: 'rlm-tools-bsl',
        adapter_version: 'rlm-index/v1',
        engine_version: '1.30.0',
        bsl_file_count: 5,
        status: 'unavailable',
        capabilities: [],
        route_priorities: {},
        failure_code: 'backend.executable_unavailable',
        failure_summary: 'approved launcher is unavailable',
      },
    ],
    configuration: schema3IndexConfiguration([
        { adapter_id: 'bsl-analyzer', engine_version: '0.2.63' },
        { adapter_id: 'rlm-tools-bsl', engine_version: '1.30.0' },
      ]),
    configuration_fingerprint: 'sha256:index-config',
  });
  await page.getByRole('navigation', { name: 'Этапы диспетчера' })
    .getByRole('button', { name: 'Подготовка различий' }).click();
  await page.getByRole('button', { name: 'Проверить индексы' }).click();
  await page.getByRole('button', { name: /Диагностика по объектам/ }).click();
  await expect(page.getByText(/bsl-analyzer 0.2.63/)).toBeVisible();
  await expect(page.getByText(/rlm-tools-bsl 1.30.0/)).toBeVisible();
  await expect(page.getByText(/backend.executable_unavailable/)).toBeVisible();
  await page.getByRole('button', { name: 'Проверить готовность' }).click();
  await expect.poll(() => ledger.actionRequests.length).toBe(1);
  expect(ledger.actionRequests[0]).toMatchObject({
    method: 'POST',
    body: {
      operation: 'indexes.build',
      payload: { mode: 'validate' },
      expected_fingerprint: 'sha256:fixture',
    },
  });
  expect(ledger.actionRequests[0].idempotencyKey).toBeTruthy();
});

test('degraded SSE reconciles the projection after five seconds', async ({ page }) => {
  const ledger = await openFixture(page);
  const before = ledger.workflowRequests();
  await ledger.degradeStream();
  await expect(page.getByText('Поток событий временно недоступен, выполняется переподключение')).toBeVisible();
  await expect.poll(() => ledger.workflowRequests(), { timeout: 6_500 }).toBeGreaterThan(before);
  await ledger.recoverStream();
  await expect(page.getByText('Поток событий временно недоступен, выполняется переподключение')).toBeHidden();
  const recovered = ledger.workflowRequests();
  await page.waitForTimeout(5_500);
  expect(ledger.workflowRequests()).toBe(recovered);
});

test('dispatcher keeps stale projection read-only until an explicit nondecreasing snapshot succeeds', async ({ page }) => {
  const ledger = await openFixture(page);
  const node = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="analyzer-1"]');
  await node.getByRole('button', { name: /Фактические анализаторы/ }).click();
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

test('active projection is distinct and only confirmed work is active', async ({ page }) => {
  expect(activeProjection).not.toEqual(saturatedProjection);
  await openFixture(page, activeProjection);
  await assertDispatcherNewGeometry(page, true);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toContainText('В работе');
  await expect(page.locator('[data-zone="form-coordinator"]')).toContainText('Ожидает');
});

test('the dispatcher renders every classifier lifecycle state through the shared panel', async ({ page }) => {
  const ledger = await openFixture(page);
  const cases = [
    ['ready', undefined, 'Готово', 'не захвачена'],
    ['active', 'running', 'В работе', 'fixture'],
    ['error', 'failed', 'Ошибка', 'Ошибка'],
    ['blocked', 'resumable', 'Ожидает', 'Остановлен'],
    ['complete', undefined, 'Готово', 'не захвачена'],
    ['unknown', 'stale', 'Недоступно', 'Устарел'],
  ] as const;
  let sequence = 1;
  for (const [circuitState, leaseState, stageLabel, leaseLabel] of cases) {
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
    const stage = page.locator('[data-testid="dispatcher-new-canvas"] .react-flow__node[data-id="classify"]');
    await expect(stage).toContainText(stageLabel);
    await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Формирование пакетов' }).click();
    const panel = page.getByRole('region', { name: 'Текущее задание' });
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(leaseLabel);
    await expect(page.getByRole('heading', { name: 'Формирование пакетов' })).toBeVisible();
    await page.keyboard.press('Escape');
  }
});

test('dispatcher exposes factual error state and keyboard interaction without losing the shell', async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await openFixture(page, errorProjection);
  await expect(page.locator('[data-zone="diffs-build"]')).toContainText('Ошибка');
  await page.getByRole('button', { name: 'Диспетчер', exact: true }).focus();
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
  await expect(page.getByRole('region', { name: /содержимое/ }).first()).toHaveAttribute('tabindex', '0');
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});

test('dispatcher empty projection is structural before visual approval', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openFixture(page, emptyProjection);
  await assertDispatcherNewGeometry(page, true);
  await assertNoClippedNodeContent(page);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(0);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-empty-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-new-five-stage-empty-1920x1080.png');
});

test('dispatcher saturated projection is factual, bounded and candidate-ready at 1920x1080', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openFixture(page);
  await assertDispatcherNewGeometry(page, true);
  await assertNoClippedNodeContent(page);
  await expect(page.locator('[data-zone="analyze-workers"], [data-zone="form-coordinator"], [data-zone="form-groupers"], [data-zone="classify-workers"], [data-zone="decide-researchers"]')).toHaveCount(5);
  await expect(page.locator('[data-invocation-id]')).toHaveCount(0);
  await expect(page.locator('[data-zone="analyze-workers"]')).toContainText('Вызовов:');
  await expect(page.locator('[data-zone="decide-target-base"]')).toContainText('Ожидают одобрения: 1');
  await expect(page.locator('[data-zone="classify-input"]')).toContainText('MRQ: 6');
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-saturated-1920x1080-candidate.png'), animations: 'disabled' });
    return;
  }
  await assertApprovedOrStructuralCandidate(page, 'dispatcher-new-five-stage-saturated-1920x1080.png');
});

test('dispatcher remains reachable through local overview at 1280x720', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await openFixture(page);
  const geometry = await assertDispatcherNewGeometry(page);
  expect(geometry.scroll.scrollWidth).toBe(geometry.scroll.clientWidth);
  assertApprovedAsset('dispatcher-new-five-stage-saturated-1280x720.png');
  const canvas = page.getByTestId('dispatcher-new-canvas');
  const viewport = page.getByTestId('dispatcher-new-canvas').locator('.react-flow__viewport');
  await canvas.locator('.react-flow__controls-zoomout').click({ force: true });
  await expect(viewport).not.toHaveAttribute('style', /scale\(0\.9\)/);
  const beforePan = await viewport.getAttribute('style');
  const canvasBox = await canvas.boundingBox();
  await page.mouse.move(canvasBox!.x + canvasBox!.width / 2, canvasBox!.y + canvasBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(canvasBox!.x + canvasBox!.width / 2 + 80, canvasBox!.y + canvasBox!.height / 2 + 40, { steps: 5 });
  await page.mouse.up();
  await expect.poll(() => viewport.getAttribute('style')).not.toBe(beforePan);
  await canvas.locator('.react-flow__controls-fitview').click({ force: true });
  await expect.poll(async () => page.evaluate(() => {
    const bounds = document.querySelector<HTMLElement>('[data-testid="dispatcher-new-canvas"]')!.getBoundingClientRect();
    return [...document.querySelectorAll<HTMLElement>('[data-testid="dispatcher-new-canvas"] .react-flow__node-stage')]
      .every((node) => {
        const box = node.getBoundingClientRect();
        return box.left >= bounds.left && box.right <= bounds.right && box.top >= bounds.top && box.bottom <= bounds.bottom;
      });
  })).toBe(true);
  await assertNoClippedNodeContent(page);
  if (process.env.UPDATE_FIVE_STAGE_VISUALS === '1') {
    await page.screenshot({ path: path.join(CHANGE_ASSETS, 'dispatcher-new-five-stage-saturated-1280x720-candidate.png'), animations: 'disabled' });
    return;
  }
});

test('dispatcher agent focus falls back after SSE removal and its local failure keeps the shell', async ({ page }) => {
  const ledger = await openFixture(page);
  const analyzer = page.locator('.react-flow__node[data-id="analyzer-1"]');
  await analyzer.locator('[data-dispatcher-kind="role"]').click();
  await page.getByRole('complementary', { name: 'Сведения диспетчера' }).getByRole('button', { name: /Вызов invocation-01/ }).click();
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
  (broken.items as unknown as { dif_queue: unknown }).dif_queue = null;
  await ledger.publish(broken, 2);
  await expect(page.getByText(/Холст диспетчера недоступен/)).toBeVisible();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Этапы диспетчера' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Журнал', exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Реестры', exact: true }).first()).toBeVisible();
  await expect(page.getByText('Легенда', { exact: true })).toBeVisible();
  await expect(page.getByTestId('dispatcher-new-canvas')).toHaveCount(0);
});

test('return from a non-working view requires a fresh snapshot and fails closed', async ({ page }) => {
  const ledger = await openFixture(page);
  await page.getByRole('button', { name: 'Источники', exact: true }).first().click();
  await expect(page.getByTestId('dispatcher-new-canvas')).toHaveCount(0);
  await expect.poll(() => ledger.closedEventSources()).toBe(1);

  ledger.failNextWorkflow();
  await page.getByRole('button', { name: 'Диспетчер', exact: true }).click();
  await expect(page.getByText('fresh snapshot unavailable')).toBeVisible();
  await expect(page.getByTestId('dispatcher-new-canvas')).toHaveCount(0);
  expect(await ledger.eventSources()).toBe(1);

  const fresh = structuredClone(saturatedProjection);
  fresh.revision += 10;
  ledger.setProjection(fresh);
  await page.getByRole('button', { name: 'Повторить снимок' }).click();
  await expect(page.getByText(`Ревизия ${fresh.revision}`, { exact: true })).toBeVisible();
  await expect(page.getByTestId('dispatcher-new-canvas')).toBeVisible();
  expect(ledger.workflowRequests()).toBe(3);
  expect(await ledger.eventSources()).toBe(2);
});

test('the dispatcher uses contextual and compact navigation with a fresh return lifecycle', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const ledger = await openFixture(page);
  await expect(page.getByRole('button', { name: 'Пример React Flow' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Обогащённая схема' })).toHaveCount(0);
  let expectedStreams = 1;
  const returnTo = async () => {
    await page.getByRole('button', { name: 'Диспетчер', exact: true }).click();
    expectedStreams += 1;
    await expect.poll(() => ledger.eventSources()).toBe(expectedStreams);
    await expect.poll(() => ledger.closedEventSources()).toBe(expectedStreams - 1);
  };
  await page.getByRole('button', { name: 'Профили и параметры', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Профили и параметры' })).toBeVisible();
  await returnTo();
  await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Подготовка различий' }).click();
  await page.getByRole('button', { name: 'Настроить источники' }).click();
  await expect(page.getByRole('heading', { name: 'Источники' })).toBeVisible();
  await returnTo();

  await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Подготовка различий' }).click();
  await page.getByRole('button', { name: 'Проверить индексы' }).click();
  await expect(page.getByText(/Индексы — одноразовое ускорение/)).toBeVisible();
  await returnTo();

  await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Анализ DIF' }).click();
  await page.getByRole('region', { name: 'Текущее задание' }).getByRole('button', { name: 'Профили и параметры' }).click();
  await expect(page.getByRole('heading', { name: 'Профили и параметры' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Этапы' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('Следующее типизированное действие')).toHaveCount(0);
  await page.setViewportSize({ width: 1280, height: 720 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.getByRole('tab', { name: 'Профили' }).click();
  await expect(page.getByText('Профили агентов')).toBeVisible();
  await page.setViewportSize({ width: 1920, height: 1080 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await returnTo();

  await page.getByRole('button', { name: 'Журнал', exact: true }).first().click();
  await expect(page.getByRole('textbox', { name: 'Поиск в событиях и журналах' })).toBeVisible();
  await returnTo();

  await page.getByRole('button', { name: 'Реестры', exact: true }).first().click();
  await expect(page.getByRole('combobox', { name: 'Реестр' })).toBeVisible();
  await returnTo();
  expect(ledger.workflowRequests()).toBe(7);
  expect(pageErrors.filter(message => message !== 'project bookmark not found')).toEqual([]);
});

test('extension review covers included, excluded, dormant, mixed-role and keyboard states', async ({ page }) => {
  const present = (name: string, active = false) => ({ present: true, name, version: '1', active });
  const absent = { present: false, name: '', version: '', active: false };
  const rows = [
    {
      uuid: '11111111-1111-1111-1111-111111111111',
      decision: 'include', rationale: '', dormant: false,
      roles: { vendor_baseline: present('Included'), target_cf: present('Included', true), next_vendor: absent },
    },
    {
      uuid: '22222222-2222-2222-2222-222222222222',
      decision: 'exclude', rationale: 'Out of scope', dormant: false,
      roles: { vendor_baseline: absent, target_cf: present('Excluded'), next_vendor: absent },
    },
    {
      uuid: '33333333-3333-3333-3333-333333333333',
      decision: 'exclude', rationale: 'Dormant policy', dormant: true,
      roles: { vendor_baseline: absent, target_cf: absent, next_vendor: absent },
    },
    {
      uuid: '44444444-4444-4444-4444-444444444444',
      decision: '', rationale: '', dormant: false,
      roles: { vendor_baseline: absent, target_cf: present('New'), next_vendor: present('Renamed', true) },
    },
  ];
  await openFixture(page, saturatedProjection, {
    profiles: [],
    infobases: { acquisition_profile: '', roles: {} },
    infobases_fingerprint: 'sha256:fixture',
    extension_scope: {
      extensions: rows,
      included: [rows[0].uuid],
      excluded: [rows[1].uuid],
      dormant: [rows[2].uuid],
      unreviewed: [rows[3].uuid],
    },
    external_artifacts: { artifacts: [] },
    upload_draft_fingerprint: 'sha256:fixture',
    connection_profiles: {},
    active_source: {},
    active_diff: {},
  });
  await page.getByRole('navigation', { name: 'Этапы диспетчера' }).getByRole('button', { name: 'Подготовка различий' }).click();
  await page.getByRole('button', { name: 'Настроить источники' }).click();
  await expect(page.locator(`#extension-${rows[3].uuid}`)).toBeFocused();
  await expect(page.getByText('Ранее принятые решения')).toBeVisible();
  await expect(page.getByText(/Новая версия поставщика: Renamed 1; активно/)).toBeVisible();
  const decision = page.getByRole('combobox', { name: `Решение для расширения ${rows[3].uuid}` });
  await decision.focus();
  await decision.selectOption('exclude');
  const rationale = page.getByLabel(`Обоснование исключения для расширения ${rows[3].uuid}`);
  await decision.press('Tab');
  await expect(rationale).toBeFocused();
  await rationale.fill('Not part of this research');
  await expect(page.getByText('Расширение не будет опубликовано в поколении исходников и не создаст DIF.').last()).toBeVisible();
});

test('empty extension discovery is distinct from uploaded external files', async ({ page }) => {
  await openFixture(page);
  await page.getByRole('button', { name: 'Источники', exact: true }).click();
  await page.getByRole('button', { name: /Расширения конфигурации/ }).click();
  await expect(page.getByText('В проверенных базах расширения не обнаружены.')).toBeVisible();
  await expect(page.getByText('В контракте нет внешних файлов')).toBeVisible();
});
