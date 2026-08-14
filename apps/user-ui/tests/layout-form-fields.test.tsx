import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LayoutFormFields } from "@/components/LayoutFormFields";
import type { LayoutData } from "@/lib/api";

function makeLayout(overrides: Partial<LayoutData> = {}): LayoutData {
  return {
    rows: [{ columns: [{ attribute: "kunde", label: "Kunde", required: false }] }],
    responsive_breakpoint_px: 480,
    is_custom: true,
    ...overrides,
  };
}

describe("LayoutFormFields - CSS-Container-Query statt Fensterbreite (P23-S6)", () => {
  it("misst die Breite des eigenen Panels statt der Fensterbreite über eine @container-Regel", () => {
    const layout = makeLayout();
    const { container } = render(
      <LayoutFormFields layout={layout} renderField={(field) => <span>{field.label}</span>} />
    );

    const wrapper = container.querySelector("[data-layout-container]");
    expect(wrapper).toBeInTheDocument();
    const containerId = wrapper!.getAttribute("data-layout-container");
    expect(containerId).toBeTruthy();

    const styleTag = container.querySelector("style");
    expect(styleTag).toBeInTheDocument();
    expect(styleTag!.textContent).toContain("@container (max-width: 479px)");
    expect(styleTag!.textContent).toContain(`[data-layout-container="${containerId}"]`);
    expect(styleTag!.textContent).not.toContain("window.innerWidth");

    expect(screen.getByText("Kunde")).toBeInTheDocument();
  });

  it("scoped den Schwellwert pro Instanz, damit zwei gleichzeitig gerenderte Layouts sich nicht überschreiben", () => {
    const layoutA = makeLayout({ responsive_breakpoint_px: 400 });
    const layoutB = makeLayout({ responsive_breakpoint_px: 900 });

    const { container } = render(
      <>
        <LayoutFormFields layout={layoutA} renderField={(field) => <span>A-{field.label}</span>} />
        <LayoutFormFields layout={layoutB} renderField={(field) => <span>B-{field.label}</span>} />
      </>
    );

    const wrappers = container.querySelectorAll("[data-layout-container]");
    expect(wrappers).toHaveLength(2);
    const idA = wrappers[0].getAttribute("data-layout-container");
    const idB = wrappers[1].getAttribute("data-layout-container");
    expect(idA).not.toBe(idB);

    const styles = Array.from(container.querySelectorAll("style")).map((s) => s.textContent ?? "");
    expect(styles.some((s) => s.includes("399px") && s.includes(`"${idA}"`))).toBe(true);
    expect(styles.some((s) => s.includes("899px") && s.includes(`"${idB}"`))).toBe(true);
  });
});
