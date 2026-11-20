import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The stills themselves are verified with stageshow's film tooling
// (libs/dms-showcase/user-ui-empty-*/REHEARSAL.md); here only the host's
// rules from ADR 0237 (P83-S4) are tested, with next/dynamic stubbed.
const received: { theme?: string }[] = [];
vi.mock("next/dynamic", () => ({
  default: () =>
    function IllustrationStub(props: { theme: string }) {
      received.push(props);
      return <div data-testid="illustration-stub" />;
    },
}));

import { EmptyState } from "@/components/EmptyStateIllustration";

// A controllable ResizeObserver: report a width for every observed element
let paneWidth = 0;
class FakeResizeObserver {
  constructor(private callback: ResizeObserverCallback) {}
  observe() {
    this.callback([{ contentRect: { width: paneWidth } } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  disconnect() {}
  unobserve() {}
}

function renderEmptyState() {
  return render(
    <EmptyState kind="preview">
      <p>Kein Dokument ausgewählt.</p>
    </EmptyState>
  );
}

describe("EmptyState", () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    received.length = 0;
    document.documentElement.dataset.theme = "light";
  });

  afterEach(() => {
    globalThis.ResizeObserver = originalResizeObserver;
    document.documentElement.removeAttribute("data-theme");
  });

  it("always renders the pane's own sentence", () => {
    renderEmptyState();
    expect(screen.getByText("Kein Dokument ausgewählt.")).toBeTruthy();
  });

  it("renders no picture without ResizeObserver (width unknown)", () => {
    // @ts-expect-error - simulating an environment without ResizeObserver
    globalThis.ResizeObserver = undefined;
    renderEmptyState();
    expect(screen.queryByTestId("empty-illustration-preview")).toBeNull();
  });

  it("renders no picture below 304px of content width (would scale under 0.95)", () => {
    paneWidth = 300;
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    renderEmptyState();
    expect(screen.queryByTestId("empty-illustration-preview")).toBeNull();
    expect(received).toHaveLength(0);
  });

  it("renders the picture aria-hidden above the sentence in a wide pane, following the theme", () => {
    paneWidth = 480;
    document.documentElement.dataset.theme = "dark";
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    renderEmptyState();
    const figure = screen.getByTestId("empty-illustration-preview");
    expect(figure.getAttribute("aria-hidden")).toBe("true");
    expect(received.at(-1)?.theme).toBe("dark");
    // The picture comes first, the sentence after it
    expect(figure.compareDocumentPosition(screen.getByText("Kein Dokument ausgewählt."))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING
    );
  });

  it("renders the centered sentence only, never a picture, without a kind", () => {
    paneWidth = 480;
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    render(
      <EmptyState>
        <p>Kein Dokument ausgewählt.</p>
      </EmptyState>
    );
    expect(screen.getByText("Kein Dokument ausgewählt.")).toBeTruthy();
    expect(screen.queryByTestId(/empty-illustration/)).toBeNull();
    expect(received).toHaveLength(0);
  });

  it("renders no picture in the high-contrast theme, also when switched at runtime", async () => {
    paneWidth = 480;
    globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
    renderEmptyState();
    expect(screen.getByTestId("empty-illustration-preview")).toBeTruthy();
    await act(async () => {
      document.documentElement.dataset.theme = "high-contrast";
      await Promise.resolve();
    });
    expect(screen.queryByTestId("empty-illustration-preview")).toBeNull();
    expect(screen.getByText("Kein Dokument ausgewählt.")).toBeTruthy();
  });
});
