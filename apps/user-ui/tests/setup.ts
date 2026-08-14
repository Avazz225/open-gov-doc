import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom does not implement `matchMedia` (Node has no real rendering) -
// `ThemeProvider` (P4-S6) needs it for the "Automatic" resolution.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}

// jsdom does not implement `ResizeObserver` - `PreviewPane` (P5-S3) uses it
// to measure the rendered image height for the OCR overlay font size.
if (typeof window !== "undefined" && !window.ResizeObserver) {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  window.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// jsdom's `Blob` does not implement `.text()` - `PreviewPane` (P5d-S2) uses it
// to read the content for client-side direct text display.
if (typeof Blob !== "undefined" && !Blob.prototype.text) {
  Blob.prototype.text = function (this: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(this);
    });
  };
}

afterEach(() => {
  cleanup();
});
