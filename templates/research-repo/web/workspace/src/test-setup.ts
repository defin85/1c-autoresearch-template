import '@testing-library/jest-dom/vitest';

// jsdom не реализует ``ResizeObserver`` и ``window.matchMedia``; React Flow и
// компонент диспетчера используют их. Подкладываем минимальные заглушки только
// для тестовой среды.

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

if (typeof window !== 'undefined' && typeof window.DOMMatrix === 'undefined') {
  // React Flow использует DOMMatrix для измерений; заглушка возвращает единичную матрицу
  window.DOMMatrix = class {
    constructor() {
      // минимальная identity-матрица
    }
    multiply() {
      return this;
    }
  } as unknown as typeof DOMMatrix;
}

if (typeof window !== 'undefined' && typeof window.DOMMatrixReadOnly === 'undefined') {
  window.DOMMatrixReadOnly = window.DOMMatrix as unknown as typeof DOMMatrixReadOnly;
}
