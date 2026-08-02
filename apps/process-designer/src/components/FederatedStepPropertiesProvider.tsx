// Eigener Properties-Panel-Provider für einen föderierten Prozessschritt
// (7.4, P6-S9) - kein eigenes BPMN-Element, sondern eine zusätzliche Gruppe
// auf jedem `bpmn:ManualTask`, die `bpmn:extensionElements/camunda:properties`
// (`taskType`/`targetInstallationId`/`targetProcessType`) liest/schreibt -
// exakt das Format, das `workflow-service`s `dispatch_pending_federation_tasks`
// als föderierten Schritt erkennt (siehe
// `services/workflow-service/src/workflow_service/main.py`). Gleiches Muster
// wie `SignatureTaskPropertiesProvider.tsx` (P6-S7/P6-S8) - bewusst dupliziert
// statt eine gemeinsame Abstraktion einzuführen (zwei unabhängige, kleine
// Provider sind einfacher nachvollziehbar als eine vorzeitig geteilte
// Hilfsschicht für zwei Anwendungsfälle).
//
// Die Gruppe erscheint nur, wenn `installations` (statisch injiziert beim
// Erzeugen des Modelers, siehe `BpmnDesigner.tsx`) nicht leer ist - erfüllt
// Konzept 7.1 "bietet der Process Designer föderierte Prozessschritte gar
// nicht erst als Auswahlmöglichkeit an", wenn kein Hub konfiguriert ist bzw.
// keine Installationen bekannt sind.
/* eslint-disable @typescript-eslint/no-explicit-any -- bpmn-js selbst typisiert
   `Moddle`/`ModdleElement` als `any` (node_modules/bpmn-js/lib/model/Types.d.ts),
   `bpmn-js-properties-panel`/`@bpmn-io/properties-panel` liefern gar keine
   Typdeklarationen (siehe src/types/untyped-modules.d.ts) - `any` ist hier die
   von der Bibliothek selbst vorgegebene Grenze, keine Abkürzung. */
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

/** Schreibt `taskType`/`targetInstallationId`/`targetProcessType` als
 * `camunda:Property`-Trio - legt `bpmn:ExtensionElements`/`camunda:Properties`
 * bei Bedarf neu an (gleiches Muster wie `SignatureTaskPropertiesProvider`). */
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
  // Statisch beim Erzeugen des Modelers injiziert (siehe BpmnDesigner.tsx) -
  // kein Live-Nachladen während einer Bearbeitungssitzung nötig.
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

// Für Tests: reine Lesefunktionen ohne DOM/bpmn-js-Instanziierung.
export { getTargetInstallationId, getTargetProcessType, isFederatedStepEnabled };
