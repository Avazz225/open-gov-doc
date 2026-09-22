import { describe, expect, it, vi } from "vitest";
import { getDecisionRef } from "@/components/DmnValidationPropertiesProvider";

// Same mock pattern as federated-step-properties-provider.test.ts - only
// the pure read function is tested here, no real DOM/bpmn-js.
vi.mock("bpmn-js-properties-panel", () => ({ useService: vi.fn() }));
vi.mock("@bpmn-io/properties-panel", () => ({ DescriptionEntry: vi.fn(), Group: vi.fn() }));

function fakeElement(decisionRef: string | undefined) {
  return {
    get: (key: string) => (key === "decisionRef" ? decisionRef : undefined),
  };
}

describe("DmnValidationPropertiesProvider - pure read helpers", () => {
  it("getDecisionRef returns an empty string when unset", () => {
    expect(getDecisionRef(fakeElement(undefined))).toBe("");
  });

  it("getDecisionRef reads the stored decisionRef value", () => {
    expect(getDecisionRef(fakeElement("approval-level"))).toBe("approval-level");
  });
});
