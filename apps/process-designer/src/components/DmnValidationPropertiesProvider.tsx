// Design-time DMN decisionRef validation (7.1, P71-S3) - closes the gap
// `extract_decision_refs` (P64-S1) only ever covered at DELETE time: a
// `bpmn:businessRuleTask`'s `camunda:decisionRef` was never checked
// against the currently loaded DMN families while editing a diagram, only
// when SpiffWorkflow actually tries to resolve it at instance-start time
// (a runtime `ValidationException`, surfaced far too late to be useful
// design-time feedback). Deliberately a passive, read-only warning
// alongside the existing built-in `decisionRef` field
// (`CamundaPlatformPropertiesProviderModule`, registered in
// `BpmnDesigner.tsx`) rather than a second editable field for the same
// underlying attribute - editing still happens exactly where it already
// did, this only adds a non-blocking hint group beneath it, same
// "client-side WARNING only" precedent as
// `FederatedStepPropertiesProvider.tsx`'s `TargetProcessTypeField`.
/* eslint-disable @typescript-eslint/no-explicit-any -- bpmn-js itself types
   `Moddle`/`ModdleElement` as `any`, see FederatedStepPropertiesProvider.tsx
   for the same justification. */
import { getBusinessObject, is } from "bpmn-js/lib/util/ModelUtil";
import { DescriptionEntry, Group } from "@bpmn-io/properties-panel";
import { useService } from "bpmn-js-properties-panel";

function getDecisionRef(element: any): string {
  const businessObject = getBusinessObject(element);
  return businessObject.get("decisionRef") ?? "";
}

function DmnValidationWarning(props: { element: any }) {
  const { element } = props;
  // Statically injected when creating the modeler (see BpmnDesigner.tsx) -
  // the `decision_id` of every currently loaded DMN family (not `name` -
  // `camunda:decisionRef` resolves against `decision_id`, see
  // `services/workflow-service/src/workflow_service/models.py`'s
  // `DmnDefinition` docstring). No live reloading during an editing
  // session, same convention as `federationInstallations`.
  const knownDecisionIds: string[] = useService("knownDecisionIds");
  const decisionRef = getDecisionRef(element);

  if (!decisionRef || knownDecisionIds.includes(decisionRef)) return null;

  // This properties panel renders via Preact (`@bpmn-io/properties-panel/
  // preact`), not this app's own React - hand-authored JSX here would
  // compile through the app's React jsx-runtime and silently fail to
  // render inside Preact's tree (found live in the browser: the group
  // appeared, correctly labeled, but its body stayed empty no matter
  // what). `DescriptionEntry` is the library's own exported component,
  // called as a plain function exactly like `CheckboxEntry`/`TextFieldEntry`
  // already are in `FederatedStepPropertiesProvider.tsx` - it already
  // produces a real Preact element internally.
  return DescriptionEntry({
    element,
    forId: "dmnValidationWarningText",
    value:
      `Keine geladene DMN-Entscheidungstabelle mit der Decision-ID ` +
      `"${decisionRef}" gefunden - dieser Business Rule Task würde beim ` +
      `Instanzstart fehlschlagen.`,
  });
}

function DmnValidationGroup(element: any, injector: any) {
  if (!is(element, "bpmn:BusinessRuleTask")) return null;
  const decisionRef = getDecisionRef(element);
  if (!decisionRef) return null;
  const knownDecisionIds: string[] = injector.get("knownDecisionIds");
  if (knownDecisionIds.includes(decisionRef)) return null;
  const translate = injector.get("translate");
  return {
    id: "dmnValidation",
    label: translate("DMN-Validierung"),
    component: Group,
    // A warning group is pointless if it stays collapsed by default like
    // every other group in this app - the whole point is that it must be
    // seen without an extra click, unlike an ordinary editable group.
    shouldOpen: true,
    entries: [{ id: "dmnValidationWarning", component: DmnValidationWarning }],
  };
}

class DmnValidationPropertiesProvider {
  private _injector: any;

  constructor(propertiesPanel: any, injector: any) {
    propertiesPanel.registerProvider(this);
    this._injector = injector;
  }

  getGroups(element: any) {
    return (groups: unknown[]) => {
      const group = DmnValidationGroup(element, this._injector);
      if (group) groups.push(group);
      return groups;
    };
  }
}
(DmnValidationPropertiesProvider as any).$inject = ["propertiesPanel", "injector"];

export const DmnValidationPropertiesProviderModule = {
  __init__: ["dmnValidationPropertiesProvider"],
  dmnValidationPropertiesProvider: ["type", DmnValidationPropertiesProvider],
};

// For tests: pure read functions without DOM/bpmn-js instantiation.
export { getDecisionRef };
