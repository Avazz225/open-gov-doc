import { describe, expect, it, vi } from "vitest";
import {
  getSignatureLevel,
  isSignatureRequired,
} from "@/components/SignatureTaskPropertiesProvider";

// Only the pure read functions are tested here - the real UI libraries
// (`bpmn-js-properties-panel`/`@bpmn-io/properties-panel`) internally pull in
// further `bpmn-js` modules on load, which do not resolve cleanly in this
// test environment without a real browser; same mocks as in
// bpmn-designer.test.tsx.
vi.mock("bpmn-js-properties-panel", () => ({ useService: vi.fn() }));
vi.mock("@bpmn-io/properties-panel", () => ({
  CheckboxEntry: vi.fn(),
  Group: vi.fn(),
  SelectEntry: vi.fn(),
  isCheckboxEntryEdited: vi.fn(),
  isSelectEntryEdited: vi.fn(),
}));

// Minimal moddle element double: `getBusinessObject`/`findExtensionElement`
// only access via `.get(key)` (see bpmn-js' `ModelUtil.getBusinessObject`
// and `bpmn:ExtensionElements`/`camunda:Properties`'s `values` list) - a real
// `bpmn-moddle` model is not needed for these pure read functions.
function fakeProperty(name: string, value: string) {
  return { $type: "camunda:Property", name, value };
}

function fakeElement(properties: Array<{ name: string; value: string }> = []) {
  const propertyValues = properties.map((p) => fakeProperty(p.name, p.value));
  const propertiesElement = {
    $type: "camunda:Properties",
    get: (key: string) => (key === "values" ? propertyValues : undefined),
  };
  const extensionValues = properties.length > 0 ? [propertiesElement] : [];
  const extensionElements =
    properties.length > 0
      ? { get: (key: string) => (key === "values" ? extensionValues : undefined) }
      : null;
  return {
    get: (key: string) => (key === "extensionElements" ? extensionElements : undefined),
  };
}

describe("SignatureTaskPropertiesProvider - pure read helpers", () => {
  it("isSignatureRequired is false without extension elements", () => {
    expect(isSignatureRequired(fakeElement())).toBe(false);
  });

  it("isSignatureRequired is true when taskType=signature is set", () => {
    const element = fakeElement([{ name: "taskType", value: "signature" }]);
    expect(isSignatureRequired(element)).toBe(true);
  });

  it("isSignatureRequired is false for an unrelated taskType value", () => {
    const element = fakeElement([{ name: "taskType", value: "other" }]);
    expect(isSignatureRequired(element)).toBe(false);
  });

  it("getSignatureLevel defaults to ses when no requiredLevel is set", () => {
    expect(getSignatureLevel(fakeElement())).toBe("ses");
  });

  it("getSignatureLevel reads the stored requiredLevel", () => {
    const element = fakeElement([
      { name: "taskType", value: "signature" },
      { name: "requiredLevel", value: "qes" },
    ]);
    expect(getSignatureLevel(element)).toBe("qes");
  });
});
