import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', workers: 1, timeout: 60_000,
  use: { baseURL: 'http://127.0.0.1:8877', trace: 'retain-on-failure' },
  webServer: {
    command: "cd ../.. && workspace_state=$(mktemp -d /tmp/one-c-autoresearch-playwright-state.XXXXXX) && ONE_C_AUTORESEARCH_WORKSPACE_HOME=$workspace_state uv run --with 'fastapi>=0.115,<1' --with 'uvicorn>=0.34,<1' python -c \"from pathlib import Path; import uvicorn; from one_c_autoresearch.workspace_api import create_app; uvicorn.run(create_app(approved_roots=[Path('/tmp')], testing=True), host='127.0.0.1', port=8877)\"",
    url: 'http://127.0.0.1:8877/api/v1/health', reuseExistingServer: false,
  },
});
