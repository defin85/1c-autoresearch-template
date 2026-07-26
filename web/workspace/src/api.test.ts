import { expect, test } from 'vitest';
import { mutationHeaders } from './api';

test('mutation headers carry idempotency key', () => {
  expect(mutationHeaders('fixed')['Idempotency-Key']).toBe('fixed');
});
