// `bpmn-js-properties-panel` and `@bpmn-io/properties-panel` provide no
// TypeScript declarations of their own (verified: no `types` field in their
// `package.json`, no `.d.ts` for the public API) - `bpmn-js` itself,
// by contrast, is fully typed (see `bpmn-js/lib/*.d.ts`) and doesn't need
// this declaration.
declare module "bpmn-js-properties-panel";
declare module "@bpmn-io/properties-panel";

// `dmn-js` (P14-S4, DMN 1.3 decision tables), like `bpmn-js-
// properties-panel`, provides no TypeScript declarations of its own for its
// modeler entry point (verified via spike build: "Could not find a
// declaration file for module 'dmn-js/lib/Modeler'").
declare module "dmn-js/lib/Modeler";
