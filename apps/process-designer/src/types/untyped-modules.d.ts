// `bpmn-js-properties-panel` und `@bpmn-io/properties-panel` liefern keine
// eigenen TypeScript-Deklarationen (verifiziert: kein `types`-Feld in ihrem
// `package.json`, keine `.d.ts` für die öffentliche API) - `bpmn-js` selbst
// ist dagegen vollständig typisiert (siehe `bpmn-js/lib/*.d.ts`) und braucht
// diese Deklaration nicht.
declare module "bpmn-js-properties-panel";
declare module "@bpmn-io/properties-panel";

// `dmn-js` (P14-S4, DMN-1.3-Entscheidungstabellen) liefert wie `bpmn-js-
// properties-panel` keine eigenen TypeScript-Deklarationen für seinen
// Modeler-Einstiegspunkt (verifiziert per Spike-Build: "Could not find a
// declaration file for module 'dmn-js/lib/Modeler'").
declare module "dmn-js/lib/Modeler";
