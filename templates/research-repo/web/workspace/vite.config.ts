import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../../src/one_c_autoresearch/workspace_static',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  test: { environment: 'jsdom', setupFiles: './src/test-setup.ts', include: ['src/**/*.test.ts', 'src/**/*.test.tsx'] },
});
