import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n";

// The kit illustration itself is verified with stageshow's film/gate tooling
// (libs/dms-showcase/migration-console-login/REHEARSAL.md); here only the host's gating
// rules from ADR 0237 are tested, so next/dynamic is replaced by a stub that
// records the props it receives.
const received: { theme?: string; strings?: Record<string, string> }[] = [];
vi.mock("next/dynamic", () => ({
  default: () =>
    function IllustrationStub(props: { theme: string; strings: Record<string, string> }) {
      received.push(props);
      return <div data-testid="illustration-stub" />;
    },
}));

import { LoginIllustration } from "@/components/LoginIllustration";

function mockViewport(wide: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("min-width") ? wide : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })) as unknown as typeof window.matchMedia;
}

function renderIllustration() {
  return render(
    <I18nProvider>
      <LoginIllustration />
    </I18nProvider>
  );
}

describe("LoginIllustration", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    received.length = 0;
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
    document.documentElement.removeAttribute("data-theme");
  });

  it("renders nothing below 1200px", () => {
    mockViewport(false);
    renderIllustration();
    expect(screen.queryByRole("img")).toBeNull();
    expect(received).toHaveLength(0);
  });

  it("renders nothing when matchMedia is unavailable", () => {
    // @ts-expect-error - simulating an environment without matchMedia
    window.matchMedia = undefined;
    renderIllustration();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("renders one text alternative with aria-hidden content when wide", () => {
    mockViewport(true);
    document.documentElement.dataset.theme = "light";
    renderIllustration();
    const figure = screen.getByRole("img");
    expect(figure.getAttribute("aria-label")).toMatch(/Landesarchiv/);
    expect(screen.getByTestId("illustration-stub").parentElement?.getAttribute("aria-hidden")).toBe("true");
    expect(received.at(-1)?.theme).toBe("light");
    // Strings come from the app's own i18n keys (interpolated where needed)
    expect(received.at(-1)?.strings?.target).toBe("Landesarchiv");
    expect(received.at(-1)?.strings?.progress).toBe("2/2 kopiert, 2 verifiziert");
  });

  it("follows the dark theme", () => {
    mockViewport(true);
    document.documentElement.dataset.theme = "dark";
    renderIllustration();
    expect(received.at(-1)?.theme).toBe("dark");
  });

  it("renders nothing in the high-contrast theme, also when switched at runtime", async () => {
    mockViewport(true);
    document.documentElement.dataset.theme = "light";
    renderIllustration();
    expect(screen.getByRole("img")).toBeTruthy();
    await act(async () => {
      document.documentElement.dataset.theme = "high-contrast";
      // Let the MutationObserver deliver
      await Promise.resolve();
    });
    expect(screen.queryByRole("img")).toBeNull();
  });
});
