// Post-Roadmap Phase 33 Session 3 (a11y test harness, ADR 0137) - `jest-axe`
// ships no Vitest-specific type declarations (only a Jest `namespace jest`
// augmentation via `@types/jest-axe`, which this project does not use) -
// same pattern `@testing-library/jest-dom/vitest`'s own `types/vitest.d.ts`
// already establishes for its matchers, applied here for `toHaveNoViolations`.
import "vitest";

declare module "vitest" {
  interface Assertion<T = unknown> {
    toHaveNoViolations(): T extends Promise<unknown> ? Promise<void> : void;
  }
  interface AsymmetricMatchersContaining {
    toHaveNoViolations(): void;
  }
}
