import { describe, expect, it, vi } from "vitest";
import {
  getSignatureLevel,
  isSignatureRequired,
} from "@/components/SignatureTaskPropertiesProvider";

// Nur die reinen Lesefunktionen werden hier getestet - die echten
// UI-Bibliotheken (`bpmn-js-properties-panel`/`@bpmn-io/properties-panel`)
// ziehen beim Laden intern weitere `bpmn-js`-Module nach, die in dieser
// Testumgebung ohne echten Browser nicht sauber auflösen; dieselben Mocks
// wie in bpmn-designer.test.tsx.
vi.mock("bpmn-js-properties-panel", () => ({ useService: vi.fn() }));
vi.mock("@bpmn-io/properties-panel", () => ({
  CheckboxEntry: vi.fn(),
  Group: vi.fn(),
  SelectEntry: vi.fn(),
  isCheckboxEntryEdited: vi.fn(),
  isSelectEntryEdited: vi.fn(),
}));

// Minimales moddle-Element-Double: `getBusinessObject`/`findExtensionElement`
// greifen nur über `.get(key)` zu (siehe bpmn-js' `ModelUtil.getBusinessObject`
// und `bpmn:ExtensionElements`/`camunda:Properties`s `values`-Liste) - ein
// echtes `bpmn-moddle`-Modell ist für diese reinen Lesefunktionen nicht nötig.
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
