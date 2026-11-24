import { describe, expect, it, vi } from "vitest";
import {
  bindingOptions,
  decodeBinding,
  encodeBinding,
  getLaneBinding,
  nextLaneProperties,
} from "@/components/LaneBindingPropertiesProvider";

// Lane bindings (P93-S1, ADR 0249) - pure helpers only, same mock pattern as
// the other properties-provider tests (no real DOM/bpmn-js).
vi.mock("bpmn-js-properties-panel", () => ({ useService: vi.fn() }));
vi.mock("@bpmn-io/properties-panel", () => ({
  Group: vi.fn(),
  SelectEntry: vi.fn(),
  isSelectEntryEdited: vi.fn(),
}));

function lane(properties: Array<{ name: string; value: string }>) {
  const values = properties.map((p) => ({ $type: "camunda:Property", ...p }));
  const props = { $type: "camunda:Properties", get: (k: string) => (k === "values" ? values : undefined) };
  const extension = properties.length
    ? { get: (k: string) => (k === "values" ? [props] : undefined) }
    : null;
  return { get: (k: string) => (k === "extensionElements" ? extension : undefined) };
}

describe("lane binding helpers", () => {
  it("reads a group, a role, or nothing", () => {
    expect(getLaneBinding(lane([{ name: "dmsGroup", value: "Sachbearbeitung" }]))).toEqual({
      kind: "group",
      name: "Sachbearbeitung",
    });
    expect(getLaneBinding(lane([{ name: "dmsRole", value: "Freigabe" }]))).toEqual({
      kind: "role",
      name: "Freigabe",
    });
    expect(getLaneBinding(lane([]))).toBeNull();
    expect(getLaneBinding(lane([{ name: "andere", value: "x" }]))).toBeNull();
  });

  it("replaces the binding and keeps other properties", () => {
    const existing = [
      { name: "andere", value: "x" },
      { name: "dmsGroup", value: "Alt" },
    ];
    expect(nextLaneProperties(existing, { kind: "role", name: "Neu" })).toEqual([
      { name: "andere", value: "x" },
      { name: "dmsRole", value: "Neu" },
    ]);
    expect(nextLaneProperties(existing, null)).toEqual([{ name: "andere", value: "x" }]);
  });

  it("encodes the select value and round-trips it", () => {
    expect(encodeBinding({ kind: "group", name: "A:B" })).toBe("group:A:B");
    expect(decodeBinding("group:A:B")).toEqual({ kind: "group", name: "A:B" });
    expect(decodeBinding("")).toBeNull();
    expect(decodeBinding("group:")).toBeNull();
    expect(decodeBinding("other:x")).toBeNull();
  });

  it("lists groups and roles and keeps an unknown current binding visible", () => {
    const labels = { unbound: "Alle", group: "Gruppe", role: "Rolle", unknown: "unbekannt" };
    const options = bindingOptions(
      { groups: ["Sachbearbeitung"], roles: ["Freigabe"] },
      { kind: "group", name: "Gelöscht" },
      labels
    );
    expect(options.map((o) => o.label)).toEqual([
      "Alle",
      "Gruppe: Sachbearbeitung",
      "Rolle: Freigabe",
      "Gruppe: Gelöscht (unbekannt)",
    ]);
  });
});
