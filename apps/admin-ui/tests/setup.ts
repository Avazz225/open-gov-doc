import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { toHaveNoViolations } from "jest-axe";
import { afterEach, expect } from "vitest";

// Automated a11y regression net (post-roadmap phase 33 session 3, ADR 0137) -
// `jest-axe` has no Vitest-specific entry point (unlike `@testing-library/
// jest-dom/vitest` above), so the matcher is registered manually against
// Vitest's own `expect` - see `jest-axe-vitest.d.ts` for the accompanying
// type augmentation.
expect.extend(toHaveNoViolations);

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

afterEach(() => {
  cleanup();
});
