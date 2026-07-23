import { expect, test } from '@playwright/test';

test('opens repository workflow without CLI and shows bounded operational views', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Исследование конфигурации 1С' })).toBeVisible();
  await page.getByRole('button', { name: 'Открыть репозиторий' }).click();
  await page.getByRole('textbox', { name: 'Название' }).fill('example');
  await page.getByRole('textbox', { name: 'Путь к репозиторию' }).fill(process.env.E2E_REPO!);
  await page.getByRole('button', { name: 'Открыть', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Диспетчер исследования' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel('Фиксированный граф автоматизации')).toHaveCount(0);
  await expect(page.getByRole('tab')).toHaveCount(0);
  await expect(page.locator('[data-id="prepare-diffs"]')).toContainText('Очередь DIF', { timeout: 30_000 });
  await expect(page.locator('[data-id="analyze-dif"]')).toContainText('Исполнители (до 4)');
  await expect(page.locator('[data-id="form-mrq"]')).toContainText('Публикация и пакеты исследования цели');
  await expect(page.locator('[data-id="decide-target"]')).toContainText('Результаты исследования цели');
  await expect(page.locator('.react-flow__edge.animated')).toHaveCount(0);
  await page.locator('[data-id="form-mrq"]').click();
  await expect(page.getByText('Формирование MRQ', { exact: true }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Запустить' })).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть' }).click();
  let previewAttempt = 0;
  await page.route('**/stage-recompute/preview', async (route) => {
    previewAttempt += 1;
    if (previewAttempt === 1) {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'workflow_stale' }) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        boundary: 'diffs',
        plan_fingerprint: 'sha256:e2e-plan',
        steps: [{ step_id: '1:diff.build', operation: 'diff.build' }],
        required_confirmations: ['confirm_recompute'],
        possible_result: 'unchanged',
        stop_before: 'mrq.discover-next',
        generations: { source: 'active-source', diff: 'active-diff' },
      }),
    });
  });
  await page.route('**/stage-recompute/runs', (route) => route.fulfill({
    status: 202,
    contentType: 'application/json',
    body: JSON.stringify({ run_id: 'e2e-run', status: 'finished', result: 'unchanged', plan_fingerprint: 'sha256:e2e-plan' }),
  }));
  await page.locator('[data-id="analyze-dif"]').click();
  await expect(page.getByRole('region', { name: 'Текущее задание' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Пересчёт этапа' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: 'Пересчитать с этапа' }).click();
  await expect(page.getByText('workflow_stale')).toBeVisible();
  await page.getByRole('button', { name: 'Пересчитать с этапа' }).click();
  await expect(page.getByLabel('Предварительный просмотр пересчёта')).toContainText('diff.build');
  await page.getByRole('checkbox', { name: 'Подтверждаю: confirm_recompute' }).check();
  await page.getByRole('button', { name: 'Запустить пересчёт' }).click();
  await expect(page.getByText('Пересчёт: unchanged')).toBeVisible();
  await page.getByRole('button', { name: 'Закрыть' }).click();
  await page.getByRole('button', { name: 'Журнал' }).first().click();
  await expect(page.getByText(/не более 500 последних событий/)).toBeVisible();
  await page.getByRole('button', { name: 'Реестры' }).click();
  await expect(page.getByText(/размер страницы 100/)).toBeVisible();
});
