import { describe, expect, it } from "vitest";
import { formatDocumentTitle, resolveKennzeichenDisplay } from "@/lib/kennzeichen";
import type { ObjectType } from "@/lib/api";

const VERTRAG: ObjectType = {
  id: 7,
  name: "Vertrag",
  applies_to: "document",
  attributes: [],
  icon: null,
  kennzeichen_display_override: null,
};

describe("resolveKennzeichenDisplay", () => {
  it("returns null when the document has no Kennzeichen attribute", () => {
    const result = resolveKennzeichenDisplay({ attributes: {}, object_type_id: 7 }, {}, true);
    expect(result).toBeNull();
  });

  it("uses the global default when the object type has no override", () => {
    const doc = { attributes: { Kennzeichen: "2026-001" }, object_type_id: 7 };
    expect(resolveKennzeichenDisplay(doc, { 7: VERTRAG }, true)).toBe("2026-001");
    expect(resolveKennzeichenDisplay(doc, { 7: VERTRAG }, false)).toBeNull();
  });

  it("lets a true override show the Kennzeichen even if the global default is off", () => {
    const doc = { attributes: { Kennzeichen: "2026-001" }, object_type_id: 7 };
    const overridden = { ...VERTRAG, kennzeichen_display_override: true };
    expect(resolveKennzeichenDisplay(doc, { 7: overridden }, false)).toBe("2026-001");
  });

  it("lets a false override hide the Kennzeichen even if the global default is on", () => {
    const doc = { attributes: { Kennzeichen: "2026-001" }, object_type_id: 7 };
    const overridden = { ...VERTRAG, kennzeichen_display_override: false };
    expect(resolveKennzeichenDisplay(doc, { 7: overridden }, true)).toBeNull();
  });

  it("falls back to the global default when the object type is unknown", () => {
    const doc = { attributes: { Kennzeichen: "2026-001" }, object_type_id: 99 };
    expect(resolveKennzeichenDisplay(doc, {}, true)).toBe("2026-001");
  });
});

describe("formatDocumentTitle", () => {
  it("prefixes the title when the Kennzeichen should be shown", () => {
    const doc = { title: "Vertrag.pdf", attributes: { Kennzeichen: "2026-001" }, object_type_id: 7 };
    expect(formatDocumentTitle(doc, { 7: VERTRAG }, true)).toBe("2026-001 Vertrag.pdf");
  });

  it("returns the plain title when the Kennzeichen should not be shown", () => {
    const doc = { title: "Vertrag.pdf", attributes: {}, object_type_id: 7 };
    expect(formatDocumentTitle(doc, { 7: VERTRAG }, true)).toBe("Vertrag.pdf");
  });
});
