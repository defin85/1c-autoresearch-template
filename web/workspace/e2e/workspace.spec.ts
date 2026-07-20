import { expect, test } from '@playwright/test';

test('completes setup, monitoring, decision, result, and verification without CLI use', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/');

  const suffix = Date.now(); const name = `E2E ${suffix}`; const projectId = `e2e-${suffix}`; const root = `/tmp/one-c-workspace-e2e-${suffix}`;
  await page.getByRole('button', { name: 'Создать или открыть проект' }).click();
  await page.getByRole('textbox', { name: 'Название' }).fill(name); await page.getByRole('textbox', { name: 'Папка проекта' }).fill(root); await page.getByRole('button', { name: 'Продолжить' }).click();
  await expect(page).toHaveURL(new RegExp(`${projectId}/setup`));

  await page.getByRole('menuitem', { name: 'Подключения' }).click();
  await page.getByRole('button', { name: 'Добавить вручную' }).click(); await page.getByRole('textbox', { name: 'Название' }).fill('Fixture'); await page.getByRole('textbox', { name: 'Адрес' }).fill('http://127.0.0.1:8877/api/v1/health'); await page.getByRole('button', { name: 'Добавить' }).click();
  const fixtureCard = page.getByText('Fixture', { exact: true }).locator('xpath=ancestor::*[contains(@class,"MuiCard-root")]'); const tested = page.waitForResponse(response => response.url().endsWith('/test') && response.request().method() === 'POST'); await fixtureCard.getByRole('button', { name: 'Проверить' }).click(); expect((await tested).ok()).toBeTruthy(); await expect(fixtureCard).toContainText('Проверено');
  const connectionId = (await page.evaluate(async () => (await fetch('/api/v1/connections')).json()))[0].id;

  await page.goto(`/#/projects/${projectId}/setup`);
  await page.getByRole('textbox', { name: 'Продукт' }).fill('Fixture product'); await page.getByRole('textbox', { name: 'Версия' }).fill('1.0'); await page.getByRole('button', { name: 'Сохранить и продолжить' }).click();
  await page.getByRole('combobox', { name: 'База поставщика' }).click(); await page.getByRole('option', { name: 'Fixture' }).click(); await page.getByRole('combobox', { name: 'База заказчика' }).click(); await page.getByRole('option', { name: 'Fixture' }).click(); await page.getByRole('button', { name: 'Сохранить и продолжить' }).click();
  await expect(page.getByText('sources/vendor_baseline', { exact: true })).toBeVisible(); await expect(page.getByText('sources/target_cf', { exact: true })).toBeVisible(); await page.getByRole('button', { name: 'Сохранить и продолжить' }).click();
  await page.getByRole('combobox').click(); await page.getByRole('option', { name: 'Только чтение' }).click(); await page.getByRole('button', { name: 'Сохранить и продолжить' }).click();
  await page.getByRole('button', { name: 'Запустить проверки' }).click(); await expect(page.getByText('Пути, проект и подключения проверены сервером')).toBeVisible(); await page.getByRole('button', { name: 'Сохранить и продолжить' }).click();
  await page.getByRole('textbox', { name: 'Включенные этапы (через запятую)' }).fill('intake'); await page.getByRole('button', { name: 'Предпросмотр project.toml' }).click(); await page.getByRole('button', { name: 'Применить и завершить' }).click();

  const intake = page.locator('#stage-intake'); await intake.getByRole('button', { name: 'Запустить' }).click(); await expect(intake).toContainText('completed', { timeout: 20_000 });
  await page.reload(); await expect(page.locator('#stage-intake')).toContainText('completed');
  await expect(page.locator('#stage-physical-cleanup')).toContainText('disabled');
  await expect(page.locator('[aria-live="polite"]')).toContainText('Завершено');
  expect(parseFloat(await page.evaluate(() => getComputedStyle(document.body).transitionDuration))).toBeLessThanOrEqual(0.00001);

  const browserMutation = async (path: string, body: unknown, idempotency = '') => page.evaluate(async ({ path, body, idempotency }) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (idempotency) headers['Idempotency-Key'] = idempotency;
    const response = await fetch(`/api/v1${path}`, { method: 'POST', headers, body: JSON.stringify(body) });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }, { path, body, idempotency });

  await browserMutation('/approvals', { project_id: projectId, stage_id: 'intake', data: { impact: 'Публикация результата', evidence: ['project.toml'], choices: ['accept', 'reject'] } }, `approval-${suffix}`);
  await page.getByRole('menuitem', { name: 'Решения' }).click(); await expect(page.getByText('Публикация результата')).toBeVisible(); await page.getByRole('button', { name: 'Принять' }).click(); await expect(page.locator('pre').last()).toContainText('accepted');

  await browserMutation(`/testing/projects/${projectId}/fixture-dashboard`, {});
  await page.goto(`/#/projects/${projectId}/stages/dashboards`); await page.getByRole('tab', { name: 'Результаты' }).click();
  const resultFrame = page.frameLocator('iframe[title^="Результат"]'); await expect(resultFrame.getByRole('heading', { name: 'Проверочный результат' })).toBeVisible();
  const verification = await browserMutation(`/projects/${projectId}/verify`, {}); expect(verification.ok, JSON.stringify(verification.doctor?.summary)).toBeTruthy();
});
