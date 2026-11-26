import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageOutlineAside, PageOutlineInline, usePageOutline } from "@/components/PageOutline";
import { I18nProvider } from "@/i18n";

// After P96-S3: the page outline lists the page's `<h2>` sections and jumps to them.

function Harness({ initial }: { initial: string[] }) {
  const ref = useRef<HTMLElement>(null);
  const outline = usePageOutline(ref);
  const [sections, setSections] = useState(initial);
  return (
    <>
      <main ref={ref}>
        <PageOutlineInline items={outline.items} activeId={outline.activeId} />
        {sections.map((title) => (
          <section key={title}>
            <h2>{title}</h2>
          </section>
        ))}
        <div role="dialog">
          <h2>Dialogtitel</h2>
        </div>
      </main>
      <PageOutlineAside items={outline.items} activeId={outline.activeId} />
      <button type="button" onClick={() => setSections((s) => [...s, "Tracing"])}>
        add
      </button>
    </>
  );
}

function renderHarness(initial: string[]) {
  return render(
    <I18nProvider>
      <Harness initial={initial} />
    </I18nProvider>
  );
}

describe("PageOutline", () => {
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView as unknown as Element["scrollIntoView"];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      cb(0);
      return 0;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists the page sections, gives them ids and skips dialog headings", async () => {
    renderHarness(["Aktive Alarme", "Grundeinstellung", "Nach Kosten"]);

    const nav = await screen.findAllByRole("navigation", { name: "Auf dieser Seite" });
    expect(nav).toHaveLength(2);
    const links = screen.getAllByRole("link", { name: "Grundeinstellung" });
    expect(links[0]).toHaveAttribute("href", "#abschnitt-grundeinstellung");
    expect(screen.getByRole("heading", { name: "Grundeinstellung" })).toHaveAttribute(
      "id",
      "abschnitt-grundeinstellung"
    );
    expect(screen.queryByRole("link", { name: "Dialogtitel" })).not.toBeInTheDocument();
  });

  it("smooth-scrolls to the section card and moves focus to its heading", async () => {
    renderHarness(["Aktive Alarme", "Nach Kosten"]);

    const [link] = await screen.findAllByRole("link", { name: "Nach Kosten" });
    fireEvent.click(link);

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(scrollIntoView.mock.contexts[0]).toBe(
      screen.getByRole("heading", { name: "Nach Kosten" }).closest("section")
    );
    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Nach Kosten" }));
    expect(window.location.hash).toBe("#abschnitt-nach-kosten");
  });

  it("picks up sections that appear later, and shows nothing for a single section", async () => {
    renderHarness(["Grundeinstellung"]);
    expect(screen.queryByRole("navigation", { name: "Auf dieser Seite" })).not.toBeInTheDocument();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "add" }));
    });

    await waitFor(() =>
      expect(screen.getAllByRole("link", { name: "Tracing" }).length).toBeGreaterThan(0)
    );
  });
});
