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
  await page.getByRole('button', { name: 'Журнал' }).first().click();
  await expect(page.getByText(/не более 500 последних событий/)).toBeVisible();
  await page.getByRole('button', { name: 'Реестры' }).click();
  await expect(page.getByText(/размер страницы 100/)).toBeVisible();
});
