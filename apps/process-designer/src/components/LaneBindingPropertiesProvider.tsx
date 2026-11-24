// Lane binding properties (P93-S1, ADR 0249): a lane can be bound to a
// group (or org unit) or a role; then only its members or the role's holders
// may claim and complete the lane's tasks. Stored on the `bpmn:Lane` as
// `camunda:properties` (`dmsGroup` / `dmsRole`) - the same extension format
// as signature and federated steps, read by workflow-service at upload
// (`spiff_adapter.parse_lane_bindings`). An unbound lane keeps today's
// behaviour. Same provider pattern as `FederatedStepPropertiesProvider.tsx`.
/* eslint-disable @typescript-eslint/no-explicit-any -- bpmn-js types its
   moddle elements as `any` (see FederatedStepPropertiesProvider.tsx). */
import { getBusinessObject, is } from "bpmn-js/lib/util/ModelUtil";
import { Group, SelectEntry, isSelectEntryEdited } from "@bpmn-io/properties-panel";
import { useService } from "bpmn-js-properties-panel";

export const LANE_GROUP_PROPERTY = "dmsGroup";
export const LANE_ROLE_PROPERTY = "dmsRole";

export interface LaneBindingOptions {
  groups: string[];
  roles: string[];
}

export type LaneBinding = { kind: "group" | "role"; name: string } | null;

function findExtensionElement(businessObject: any, type: string): any {
  const extensionElements = businessObject.get("extensionElements");
  if (!extensionElements) return null;
  return extensionElements.get("values").find((value: any) => value.$type === type);
}

/** The lane's binding, or null for an unbound lane. */
export function getLaneBinding(element: any): LaneBinding {
  const properties = findExtensionElement(getBusinessObject(element), "camunda:Properties");
  if (!properties) return null;
  const values: any[] = properties.get("values");
  const group = values.find((p) => p.name === LANE_GROUP_PROPERTY)?.value;
  if (group) return { kind: "group", name: group };
  const role = values.find((p) => p.name === LANE_ROLE_PROPERTY)?.value;
  if (role) return { kind: "role", name: role };
  return null;
}

/** The properties a lane ends up with: other properties kept, the binding
 * properties replaced (at most one of them). */
export function nextLaneProperties(
  existing: Array<{ name: string; value: string }>,
  binding: LaneBinding
): Array<{ name: string; value: string }> {
  const kept = existing.filter(
    (p) => p.name !== LANE_GROUP_PROPERTY && p.name !== LANE_ROLE_PROPERTY
  );
  if (!binding || !binding.name) return kept;
  return [
    ...kept,
    { name: binding.kind === "group" ? LANE_GROUP_PROPERTY : LANE_ROLE_PROPERTY, value: binding.name },
  ];
}

function setLaneBinding(element: any, bpmnFactory: any, commandStack: any, binding: LaneBinding) {
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
  const existing = properties
    .get("values")
    .map((p: any) => ({ name: p.name as string, value: p.value as string }));
  const nextValues = nextLaneProperties(existing, binding).map(({ name, value }) => {
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

/** The select's value for a binding ("group:<name>", "role:<name>" or ""). */
export function encodeBinding(binding: LaneBinding): string {
  return binding ? `${binding.kind}:${binding.name}` : "";
}

export function decodeBinding(value: string): LaneBinding {
  const index = value.indexOf(":");
  if (index < 0) return null;
  const kind = value.slice(0, index);
  const name = value.slice(index + 1);
  return (kind === "group" || kind === "role") && name ? { kind, name } : null;
}

/** One list: unbound, every group, every role - a name that no longer exists
 * stays visible (marked) instead of silently vanishing. */
export function bindingOptions(
  options: LaneBindingOptions,
  current: LaneBinding,
  labels: { unbound: string; group: string; role: string; unknown: string }
): Array<{ value: string; label: string }> {
  const list = [
    { value: "", label: labels.unbound },
    ...options.groups.map((name) => ({ value: `group:${name}`, label: `${labels.group}: ${name}` })),
    ...options.roles.map((name) => ({ value: `role:${name}`, label: `${labels.role}: ${name}` })),
  ];
  if (current && !list.some((o) => o.value === encodeBinding(current))) {
    const kindLabel = current.kind === "group" ? labels.group : labels.role;
    list.push({
      value: encodeBinding(current),
      label: `${kindLabel}: ${current.name} (${labels.unknown})`,
    });
  }
  return list;
}

function BindingField(props: { element: any }) {
  const { element } = props;
  const bpmnFactory = useService("bpmnFactory");
  const commandStack = useService("commandStack");
  const translate = useService("translate");
  const options: LaneBindingOptions = useService("laneBindingOptions");
  return SelectEntry({
    element,
    id: "laneBinding",
    label: translate("Zuständig"),
    getValue: () => encodeBinding(getLaneBinding(element)),
    setValue: (value: string) =>
      setLaneBinding(element, bpmnFactory, commandStack, decodeBinding(value)),
    getOptions: () =>
      bindingOptions(options, getLaneBinding(element), {
        unbound: translate("Alle (nicht gebunden)"),
        group: translate("Gruppe"),
        role: translate("Rolle"),
        unknown: translate("unbekannt"),
      }),
  });
}

function LaneBindingGroup(element: any, injector: any) {
  if (!is(element, "bpmn:Lane")) return null;
  const translate = injector.get("translate");
  return {
    id: "laneBindingGroup",
    label: translate("Zuständigkeit (Lane)"),
    component: Group,
    entries: [{ id: "laneBinding", component: BindingField, isEdited: isSelectEntryEdited }],
  };
}

class LaneBindingPropertiesProvider {
  private _injector: any;

  constructor(propertiesPanel: any, injector: any) {
    propertiesPanel.registerProvider(this);
    this._injector = injector;
  }

  getGroups(element: any) {
    return (groups: unknown[]) => {
      const group = LaneBindingGroup(element, this._injector);
      if (group) groups.push(group);
      return groups;
    };
  }
}
(LaneBindingPropertiesProvider as any).$inject = ["propertiesPanel", "injector"];

export const LaneBindingPropertiesProviderModule = {
  __init__: ["laneBindingPropertiesProvider"],
  laneBindingPropertiesProvider: ["type", LaneBindingPropertiesProvider],
};
