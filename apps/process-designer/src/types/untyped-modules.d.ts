// `bpmn-js-properties-panel` und `@bpmn-io/properties-panel` liefern keine
// eigenen TypeScript-Deklarationen (verifiziert: kein `types`-Feld in ihrem
// `package.json`, keine `.d.ts` für die öffentliche API) - `bpmn-js` selbst
// ist dagegen vollständig typisiert (siehe `bpmn-js/lib/*.d.ts`) und braucht
// diese Deklaration nicht.
declare module "bpmn-js-properties-panel";
declare module "@bpmn-io/properties-panel";
