// Dedicated properties panel provider for the Signature Task (3.10, P6-S7/P6-S8) -
// not its own BPMN element, but an additional group on every
// `bpmn:ManualTask` that reads/writes `bpmn:extensionElements/camunda:properties`
// (`taskType`/`requiredLevel`) - exactly the format that
// `workflow-service`'s `CamundaParser` has recognized as a Signature Task
// since P6-S7 (see `services/workflow-service/src/workflow_service/spiff_adapter.py`).
//
// `bpmn-js-spiffworkflow` is deliberately not used (see ADR 0026) - the
// pattern used here (group/entry registration, `bpmnFactory`,
// `commandStack.execute('element.updateModdleProperties', ...)`, nested
// `camunda:Properties`/`camunda:Property` creation) is taken 1:1 from the
// built-in `CamundaPlatformPropertiesProviderModule`'s own
// "Extension properties" group (`bpmn-js-properties-panel`,
// traced against the actually installed version 5.63.0 in the bundled
// source, not assumed from the docs) - the same, actually
// working mechanism, just with a purpose-built instead of generic
// UI (checkbox + level selection instead of a free key/value list).
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
  isCheckboxEntryEdited,
  isSelectEntryEdited,
} from "@bpmn-io/properties-panel";
// `useService` is re-exported by `bpmn-js-properties-panel`, not by
// `@bpmn-io/properties-panel` (verified against the installed packages -
// an original import from there failed the production build).
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

/** Writes `taskType`/`requiredLevel` as a `camunda:Property` pair - creates
 * `bpmn:ExtensionElements`/`camunda:Properties` anew if needed (same
 * pattern as `ExtensionPropertiesProps.addFactory` in the built-in provider). */
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

// For tests: pure read functions without DOM/bpmn-js instantiation.
export { getSignatureLevel, isSignatureRequired };
