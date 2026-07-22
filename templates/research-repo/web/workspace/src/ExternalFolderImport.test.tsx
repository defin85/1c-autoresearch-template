import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ExternalFolderImport } from './ExternalFolderImport';

describe('ExternalFolderImport', () => {
  const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'webkitdirectory');
  afterEach(() => descriptor ? Object.defineProperty(HTMLInputElement.prototype, 'webkitdirectory', descriptor) : delete (HTMLInputElement.prototype as { webkitdirectory?: boolean }).webkitdirectory);

  it('retains the individual-upload fallback when directory selection is unavailable', () => {
    delete (HTMLInputElement.prototype as { webkitdirectory?: boolean }).webkitdirectory;
    render(<ExternalFolderImport projectId="project" workflowFingerprint="fingerprint" onComplete={() => undefined} />);
    expect(screen.getByText(/используйте загрузку отдельных внешних артефактов/i)).toBeInTheDocument();
  });

  it('exposes an accessible native directory selector when supported', () => {
    Object.defineProperty(HTMLInputElement.prototype, 'webkitdirectory', { configurable: true, value: false });
    render(<ExternalFolderImport projectId="project" workflowFingerprint="fingerprint" onComplete={() => undefined} />);
    expect(screen.getByRole('button', { name: /выбрать папку/i })).toBeInTheDocument();
  });
});
