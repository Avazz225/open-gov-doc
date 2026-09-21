import { describe, expect, it, vi } from "vitest";
import {
  getTargetInstallationId,
  getTargetProcessType,
  isFederatedStepEnabled,
  validateTargetProcessType,
} from "@/components/FederatedStepPropertiesProvider";
import type { FederationInstallation } from "@/components/FederatedStepPropertiesProvider";

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

// P64-S1 (4.5): the process-designer half of the cross-service reference
// validation pattern - a client-side WARNING against the target
// installation's own declared `supported_process_types` catalog (never a
// hard rejection, see the function's own docstring).
describe("validateTargetProcessType", () => {
  const installations: FederationInstallation[] = [
    { id: "install-a", display_name: "A", supported_process_types: ["external-review"] },
    { id: "install-b", display_name: "B", supported_process_types: [] },
  ];

  it("is silent for an empty value (nothing entered yet)", () => {
    expect(validateTargetProcessType("", installations, "install-a")).toBeUndefined();
  });

  it("is silent when the target installation declares no restriction (empty catalog)", () => {
    expect(
      validateTargetProcessType("anything-at-all", installations, "install-b")
    ).toBeUndefined();
  });

  it("is silent when no target installation is selected yet", () => {
    expect(validateTargetProcessType("external-review", installations, "")).toBeUndefined();
  });

  it("is silent when the value matches the target's declared catalog", () => {
    expect(
      validateTargetProcessType("external-review", installations, "install-a")
    ).toBeUndefined();
  });

  it("warns when the value is not in the target's declared, non-empty catalog", () => {
    const message = validateTargetProcessType("unknown-type", installations, "install-a");
    expect(message).toContain("external-review");
  });
});
