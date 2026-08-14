// Dedicated properties panel provider for a federated process step
// (7.4, P6-S9) - not its own BPMN element, but an additional group
// on every `bpmn:ManualTask` that reads/writes `bpmn:extensionElements/camunda:properties`
// (`taskType`/`targetInstallationId`/`targetProcessType`) - exactly the format
// that `workflow-service`'s `dispatch_pending_federation_tasks`
// recognizes as a federated step (see
// `services/workflow-service/src/workflow_service/main.py`). Same pattern
// as `SignatureTaskPropertiesProvider.tsx` (P6-S7/P6-S8) - deliberately
// duplicated instead of introducing a shared abstraction (two independent,
// small providers are easier to follow than a prematurely shared
// helper layer for two use cases).
//
// The group only appears when `installations` (statically injected when
// creating the modeler, see `BpmnDesigner.tsx`) is not empty - fulfills
// concept 7.1 "the process designer does not even offer federated process
// steps as an option" when no hub is configured or
// no installations are known.
/* eslint-disable @typescript-eslint/no-explicit-any -- bpmn-js itself types
   `Moddle`/`ModdleElement` as `any` (node_modules/bpmn-js/lib/model/Types.d.ts),
   `bpmn-js-properties-panel`/`@bpmn-io/properties-panel` provide no
   type declarations at all (see src/types/untyped-modules.d.ts) - `any` here is
   the boundary imposed by the library itself, not a shortcut. */
import { getBusinessObject, is } from "bpmn-js/lib/util/ModelUtil";
import {
  CheckboxEntry,
  Group,
  SelectEntry,
  TextFieldEntry,
  isCheckboxEntryEdited,
  isSelectEntryEdited,
  isTextFieldEntryEdited,
} from "@bpmn-io/properties-panel";
import { useService } from "bpmn-js-properties-panel";

export interface FederationInstallation {
  id: string;
  display_name: string;
}

function findExtensionElement(businessObject: any, type: string): any {
  const extensionElements = businessObject.get("extensionElements");
  if (!extensionElements) return null;
  return extensionElements
    .get("values")
    .find((value: any) => value.$type === type);
}

function findProperty(propertiesElement: any, name: string): any {
  if (!propertiesElement) return null;
  return propertiesElement.get("values").find((value: any) => value.name === name);
}

function getFederatedPropertyValue(element: any, name: string): string | undefined {
  const businessObject = getBusinessObject(element);
  const properties = findExtensionElement(businessObject, "camunda:Properties");
  return findProperty(properties, name)?.value;
}

function isFederatedStepEnabled(element: any): boolean {
  return getFederatedPropertyValue(element, "taskType") === "federated";
}

function getTargetInstallationId(element: any): string {
  return getFederatedPropertyValue(element, "targetInstallationId") ?? "";
}

function getTargetProcessType(element: any): string {
  return getFederatedPropertyValue(element, "targetProcessType") ?? "";
}

/** Writes `taskType`/`targetInstallationId`/`targetProcessType` as a
 * `camunda:Property` trio - creates `bpmn:ExtensionElements`/`camunda:Properties`
 * anew if needed (same pattern as `SignatureTaskPropertiesProvider`). */
function setFederatedStepProperties(
  element: any,
  bpmnFactory: any,
  commandStack: any,
  values: {
    taskType: string | undefined;
    targetInstallationId: string | undefined;
    targetProcessType: string | undefined;
  }
): void {
  const businessObject = getBusinessObject(element);
  const commands: unknown[] = [];

  let extensionElements = businessObject.get("extensionElements");
  if (!extensionElements) {
    extensionElements = bpmnFactory.create("bpmn:ExtensionElements", { values: [] });
    extensionElements.$parent = businessObject;
    commands.push({
      cmd: "element.updateModdleProperties",
      context: { element, moddleElement: businessObject, properties: { extensionElements } },
    });
  }

  let properties = findExtensionElement(businessObject, "camunda:Properties");
  if (!properties) {
    properties = bpmnFactory.create("camunda:Properties", { values: [] });
    properties.$parent = extensionElements;
    commands.push({
      cmd: "element.updateModdleProperties",
      context: {
        element,
        moddleElement: extensionElements,
        properties: { values: [...extensionElements.get("values"), properties] },
      },
    });
  }

  const nextValues = Object.entries(values)
    .filter(([, value]) => value !== undefined && value !== "")
    .map(([name, value]) => {
      const property = bpmnFactory.create("camunda:Property", { name, value });
      property.$parent = properties;
      return property;
    });

  commands.push({
    cmd: "element.updateModdleProperties",
    context: { element, moddleElement: properties, properties: { values: nextValues } },
  });

  commandStack.execute("properties-panel.multi-command-executor", commands);
}

function FederatedStepEnabledField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");

  const getValue = () => isFederatedStepEnabled(element);
  const setValue = (checked: boolean) => {
    setFederatedStepProperties(element, bpmnFactory, commandStack, {
      taskType: checked ? "federated" : undefined,
      targetInstallationId: checked ? getTargetInstallationId(element) : undefined,
      targetProcessType: checked ? getTargetProcessType(element) : undefined,
    });
  };

  return CheckboxEntry({
    element,
    id: "federatedStepEnabled",
    label: translate("Föderierter Schritt"),
    getValue,
    setValue,
  });
}

function TargetInstallationField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");
  // Statically injected when creating the modeler (see BpmnDesigner.tsx) -
  // no live reloading needed during an editing session.
  const installations: FederationInstallation[] = useService("federationInstallations");

  const getValue = () => getTargetInstallationId(element);
  const setValue = (value: string) => {
    setFederatedStepProperties(element, bpmnFactory, commandStack, {
      taskType: "federated",
      targetInstallationId: value,
      targetProcessType: getTargetProcessType(element),
    });
  };
  const getOptions = () =>
    installations.map((installation) => ({
      value: installation.id,
      label: installation.display_name,
    }));

  return SelectEntry({
    element,
    id: "federatedTargetInstallation",
    label: translate("Zielinstallation"),
    getValue,
    setValue,
    getOptions,
  });
}

function TargetProcessTypeField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");

  const getValue = () => getTargetProcessType(element);
  const setValue = (value: string) => {
    setFederatedStepProperties(element, bpmnFactory, commandStack, {
      taskType: "federated",
      targetInstallationId: getTargetInstallationId(element),
      targetProcessType: value,
    });
  };

  return TextFieldEntry({
    element,
    id: "federatedTargetProcessType",
    label: translate("Ziel-Prozesstyp"),
    getValue,
    setValue,
  });
}

function federatedStepEntries(element: any) {
  const entries = [
    {
      id: "federatedStepEnabled",
      component: FederatedStepEnabledField,
      isEdited: isCheckboxEntryEdited,
    },
  ];
  if (isFederatedStepEnabled(element)) {
    entries.push(
      {
        id: "federatedTargetInstallation",
        component: TargetInstallationField,
        isEdited: isSelectEntryEdited,
      },
      {
        id: "federatedTargetProcessType",
        component: TargetProcessTypeField,
        isEdited: isTextFieldEntryEdited,
      }
    );
  }
  return entries;
}

function FederatedStepGroup(element: any, injector: any) {
  if (!is(element, "bpmn:ManualTask")) return null;
  const installations: FederationInstallation[] = injector.get("federationInstallations");
  if (!installations || installations.length === 0) return null;
  const translate = injector.get("translate");
  return {
    id: "federatedStep",
    label: translate("Föderation (7.4)"),
    component: Group,
    entries: federatedStepEntries(element),
  };
}

class FederatedStepPropertiesProvider {
  private _injector: any;

  constructor(propertiesPanel: any, injector: any) {
    propertiesPanel.registerProvider(this);
    this._injector = injector;
  }

  getGroups(element: any) {
    return (groups: unknown[]) => {
      const group = FederatedStepGroup(element, this._injector);
      if (group) groups.push(group);
      return groups;
    };
  }
}
(FederatedStepPropertiesProvider as any).$inject = ["propertiesPanel", "injector"];

export const FederatedStepPropertiesProviderModule = {
  __init__: ["federatedStepPropertiesProvider"],
  federatedStepPropertiesProvider: ["type", FederatedStepPropertiesProvider],
};

// For tests: pure read functions without DOM/bpmn-js instantiation.
export { getTargetInstallationId, getTargetProcessType, isFederatedStepEnabled };
