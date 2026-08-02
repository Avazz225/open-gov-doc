// Eigener Properties-Panel-Provider für den Signature Task (3.10, P6-S7/P6-S8) -
// kein eigenes BPMN-Element, sondern eine zusätzliche Gruppe auf jedem
// `bpmn:ManualTask`, die `bpmn:extensionElements/camunda:properties`
// (`taskType`/`requiredLevel`) liest/schreibt - exakt das Format, das
// `workflow-service`s `CamundaParser` seit P6-S7 als Signature Task erkennt
// (siehe `services/workflow-service/src/workflow_service/spiff_adapter.py`).
//
// `bpmn-js-spiffworkflow` wird bewusst nicht verwendet (siehe ADR 0026) - das
// hier verwendete Muster (Gruppe/Entry-Registrierung, `bpmnFactory`,
// `commandStack.execute('element.updateModdleProperties', ...)`, verschachtelte
// `camunda:Properties`/`camunda:Property`-Erzeugung) ist 1:1 aus dem
// eingebauten `CamundaPlatformPropertiesProviderModule`s eigener
// "Extension properties"-Gruppe übernommen (`bpmn-js-properties-panel`,
// gegen die tatsächlich installierte Version 5.63.0 im gebündelten Quelltext
// nachvollzogen, nicht aus der Doku angenommen) - dieselbe, real
// funktionierende Mechanik, nur mit einer zweckgebundenen statt generischen
// Bedienoberfläche (Checkbox + Niveau-Auswahl statt freier Schlüssel/Wert-Liste).
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
  isCheckboxEntryEdited,
  isSelectEntryEdited,
} from "@bpmn-io/properties-panel";
// `useService` wird von `bpmn-js-properties-panel` reexportiert, nicht von
// `@bpmn-io/properties-panel` (verifiziert gegen die installierten Pakete -
// ein ursprünglicher Import von dort schlug beim Produktions-Build fehl).
import { useService } from "bpmn-js-properties-panel";

const SIGNATURE_LEVELS = ["ses", "aes", "qes"];
const DEFAULT_LEVEL = "ses";

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

function getSignaturePropertyValue(element: any, name: string): string | undefined {
  const businessObject = getBusinessObject(element);
  const properties = findExtensionElement(businessObject, "camunda:Properties");
  return findProperty(properties, name)?.value;
}

function isSignatureRequired(element: any): boolean {
  return getSignaturePropertyValue(element, "taskType") === "signature";
}

function getSignatureLevel(element: any): string {
  return getSignaturePropertyValue(element, "requiredLevel") ?? DEFAULT_LEVEL;
}

/** Schreibt `taskType`/`requiredLevel` als `camunda:Property`-Paar - legt
 * `bpmn:ExtensionElements`/`camunda:Properties` bei Bedarf neu an (gleiches
 * Muster wie `ExtensionPropertiesProps.addFactory` im eingebauten Provider). */
function setSignatureProperties(
  element: any,
  bpmnFactory: any,
  commandStack: any,
  values: { taskType: string | undefined; requiredLevel: string | undefined }
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
    .filter(([, value]) => value !== undefined)
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

function SignatureRequiredField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");

  const getValue = () => isSignatureRequired(element);
  const setValue = (checked: boolean) => {
    setSignatureProperties(element, bpmnFactory, commandStack, {
      taskType: checked ? "signature" : undefined,
      requiredLevel: checked ? getSignatureLevel(element) : undefined,
    });
  };

  return CheckboxEntry({
    element,
    id: "signatureRequired",
    label: translate("Signatur erforderlich"),
    getValue,
    setValue,
  });
}

function SignatureLevelField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");

  const getValue = () => getSignatureLevel(element);
  const setValue = (value: string) => {
    setSignatureProperties(element, bpmnFactory, commandStack, {
      taskType: "signature",
      requiredLevel: value,
    });
  };
  const getOptions = () =>
    SIGNATURE_LEVELS.map((level) => ({ value: level, label: level.toUpperCase() }));

  return SelectEntry({
    element,
    id: "signatureLevel",
    label: translate("Mindestniveau"),
    getValue,
    setValue,
    getOptions,
  });
}

function signatureTaskEntries(element: any) {
  const entries = [
    { id: "signatureRequired", component: SignatureRequiredField, isEdited: isCheckboxEntryEdited },
  ];
  if (isSignatureRequired(element)) {
    entries.push({
      id: "signatureLevel",
      component: SignatureLevelField,
      isEdited: isSelectEntryEdited,
    });
  }
  return entries;
}

function SignatureTaskGroup(element: any, injector: any) {
  if (!is(element, "bpmn:ManualTask")) return null;
  const translate = injector.get("translate");
  return {
    id: "signatureTask",
    label: translate("Signatur (3.10)"),
    component: Group,
    entries: signatureTaskEntries(element),
  };
}

class SignatureTaskPropertiesProvider {
  private _injector: any;

  constructor(propertiesPanel: any, injector: any) {
    propertiesPanel.registerProvider(this);
    this._injector = injector;
  }

  getGroups(element: any) {
    return (groups: unknown[]) => {
      const group = SignatureTaskGroup(element, this._injector);
      if (group) groups.push(group);
      return groups;
    };
  }
}
(SignatureTaskPropertiesProvider as any).$inject = ["propertiesPanel", "injector"];

export const SignatureTaskPropertiesProviderModule = {
  __init__: ["signatureTaskPropertiesProvider"],
  signatureTaskPropertiesProvider: ["type", SignatureTaskPropertiesProvider],
};

// Für Tests: reine Lesefunktionen ohne DOM/bpmn-js-Instanziierung.
export { getSignatureLevel, isSignatureRequired };
