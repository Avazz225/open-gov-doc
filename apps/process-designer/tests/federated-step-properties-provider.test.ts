import { describe, expect, it, vi } from "vitest";
import {
  getTargetInstallationId,
  getTargetProcessType,
  isFederatedStepEnabled,
} from "@/components/FederatedStepPropertiesProvider";

// Gleiches Mock-Muster wie signature-task-properties-provider.test.ts - nur
// die reinen Lesefunktionen werden hier getestet, kein echtes DOM/bpmn-js.
vi.mock("bpmn-js-properties-panel", () => ({ useService: vi.fn() }));
vi.mock("@bpmn-io/properties-panel", () => ({
  CheckboxEntry: vi.fn(),
  Group: vi.fn(),
  SelectEntry: vi.fn(),
  TextFieldEntry: vi.fn(),
  isCheckboxEntryEdited: vi.fn(),
  isSelectEntryEdited: vi.fn(),
  isTextFieldEntryEdited: vi.fn(),
}));

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

describe("FederatedStepPropertiesProvider - pure read helpers", () => {
  it("isFederatedStepEnabled is false without extension elements", () => {
    expect(isFederatedStepEnabled(fakeElement())).toBe(false);
  });

  it("isFederatedStepEnabled is true when taskType=federated is set", () => {
    const element = fakeElement([{ name: "taskType", value: "federated" }]);
    expect(isFederatedStepEnabled(element)).toBe(true);
  });

  it("isFederatedStepEnabled is false for an unrelated taskType value", () => {
    const element = fakeElement([{ name: "taskType", value: "signature" }]);
    expect(isFederatedStepEnabled(element)).toBe(false);
  });

  it("getTargetInstallationId defaults to an empty string when unset", () => {
    expect(getTargetInstallationId(fakeElement())).toBe("");
  });

  it("getTargetInstallationId reads the stored value", () => {
    const element = fakeElement([
      { name: "taskType", value: "federated" },
      { name: "targetInstallationId", value: "install-1" },
    ]);
    expect(getTargetInstallationId(element)).toBe("install-1");
  });

  it("getTargetProcessType reads the stored value", () => {
    const element = fakeElement([
      { name: "taskType", value: "federated" },
      { name: "targetInstallationId", value: "install-1" },
      { name: "targetProcessType", value: "external-review" },
    ]);
    expect(getTargetProcessType(element)).toBe("external-review");
  });
});
