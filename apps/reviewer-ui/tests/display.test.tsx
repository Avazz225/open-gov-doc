import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ShortKey, actionTypeLabel } from "@/lib/display";

// P83-S11 (item 6): related-object keys and action types are data, shown
// short or translated rather than as raw values.
describe("ShortKey", () => {
  it("shortens a UUID to 8 monospaced characters with the full value on hover", () => {
    const uuid = "feb5894c-e4f8-466e-9620-0e684a4d8b68";
    render(<ShortKey value={uuid} />);
    const code = screen.getByText("feb5894c");
    expect(code.tagName).toBe("CODE");
    expect(code).toHaveAttribute("title", uuid);
  });

  it("keeps the suffix of a `<uuid>:<version>` key (P86-S2)", () => {
    const key = "60ccb725-4261-40d2-a880-62e38802476a:2";
    render(<ShortKey value={key} />);
    const code = screen.getByText("60ccb725:2");
    expect(code.tagName).toBe("CODE");
    expect(code).toHaveAttribute("title", key);
  });

  it("shows a readable business key unchanged and a missing one as a dash", () => {
    const { rerender } = render(<ShortKey value="case-42" />);
    expect(screen.getByText("case-42").tagName).not.toBe("CODE");
    rerender(<ShortKey value={null} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });
});

describe("actionTypeLabel", () => {
  const dict: Record<string, string> = { "actionTypes.document.delete": "Dokument löschen" };
  const t = (path: string) => dict[path] ?? path;

  it("returns the translation for a known type and the raw type otherwise", () => {
    expect(actionTypeLabel(t, "document.delete")).toEqual({ label: "Dokument löschen", known: true });
    expect(actionTypeLabel(t, "query.custom_fix")).toEqual({ label: "query.custom_fix", known: false });
  });
});
