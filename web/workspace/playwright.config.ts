process.env.PORTABLE_TEMPLATE = '1';
import { defineConfig } from '@playwright/test';
import path from 'node:path';

const repo = path.resolve('../..');
process.env.E2E_REPO = repo;

export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 60_000,
  snapshotPathTemplate: '../../openspec/changes/add-mrq-batch-classification-stage/assets/{arg}{ext}',
  use: {
    baseURL: 'http://127.0.0.1:8877',
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `cd ../.. && uv run --extra workspace --with httpx python -c "from pathlib import Path; import tempfile, uvicorn; from one_c_autoresearch.workspace_api import create_app; uvicorn.run(create_app(Path(tempfile.mkdtemp()), [Path('${repo}')], True), host='127.0.0.1', port=8877)"`,
    url: 'http://127.0.0.1:8877/api/v1/health', reuseExistingServer: false,
  },
});
