# 0026 — Process Designer: `bpmn-js` without the `bpmn-js-spiffworkflow` addon

**Status:** accepted
**Context:** P6-S8 (Process Designer). ADR 0021 accepted `bpmn-js`/`bpmn-js-spiffworkflow` as a pair for the Process Designer, including the watermark condition mandated by the bpmn.io license. During the actual implementation of this session, `bpmn-js-spiffworkflow` was researched again (npm registry + GitHub, not taken from training data) — with a different result than at the time ADR 0021 was researched.

## Decision

The Process Designer uses **only `bpmn-js`** (still accepted as in ADR 0021, including the bpmn.io watermark) **plus `bpmn-js-properties-panel` + `@bpmn-io/properties-panel` + `camunda-bpmn-moddle`** for properties-panel functionality. **`bpmn-js-spiffworkflow` is deliberately not used.** The Signature Task introduced in P6-S7 (technically a Manual Task with `camunda:properties` extension elements, see `services/workflow-service/src/workflow_service/spiff_adapter.py`) instead gets its own small properties-panel provider (`SignatureTaskPropertiesProvider.tsx`: "Signature required" checkbox + level selection SES/AES/QES on `bpmn:ManualTask` elements), which reads/writes the same `camunda:properties` format that `workflow-service` already expects.

## Rationale

- **`bpmn-js-spiffworkflow` has not been published on npm since 2022** (`latest` = `0.0.10`), even though commits continue to be made actively on GitHub — SpiffWorkflow's own reference frontend (`spiff-arena`) therefore does not source the package from npm itself, but pins it via `github:sartography/bpmn-js-spiffworkflow#main` against a continuously changing branch. Such a pin is a reproducibility/supply-chain risk (no fixed, auditable release state) that this project should not take on.
- **License inconsistency discovered**: the published package's npm metadata states MIT, whereas the `package.json` on the current GitHub `main` branch states LGPL instead. Without being able to trustworthily resolve either of these two statements, the actual license situation is currently ambiguous — an additional reason not to take on the package as a dependency (unlike `bpmn-js` itself, ADR 0021, or SpiffWorkflow, ADR 0018, where the license situation was clear in each case).
- **No built-in "Signature Task" concept**: `bpmn-js-spiffworkflow` does not ship a ready-made UI for the project-specific Signature Task anyway — a custom properties-panel provider would have been needed regardless. The only reason to include the risky package anyway (SpiffWorkflow-specific script-editor/properties-panel support) therefore does not apply for the functionality actually needed here.
- **Pattern verified rather than assumed**: the provider/group/entry registration pattern used in `SignatureTaskPropertiesProvider.tsx` (`propertiesPanel.registerProvider`, `bpmnFactory.create`, `commandStack.execute('element.updateModdleProperties', ...)`, `properties-panel.multi-command-executor`) was traced against the actually installed, bundled source of `bpmn-js-properties-panel`/`@bpmn-io/properties-panel` (whose built-in "Extension properties" group uses exactly the same mechanism), not assumed from documentation — the same verification discipline as with SpiffWorkflow/pyHanko in earlier sessions.
- The same pragmatic trade-off as with earlier library substitutions in this project (e.g. ADR 0010, EicarSignatureEngine instead of ClamdEngine): a library proposed in the concept is replaced as soon as actual implementation reveals a concrete, documented risk that was not yet known at the time of the original decision's research (here: ADR 0021) or has worsened since.

## Consequences

- `bpmn-js` itself (including the bpmn.io watermark, ADR 0021) remains unchanged as part of the Process Designer — only the SpiffWorkflow-specific addon package is dropped.
- The Signature Task has no SpiffWorkflow-specific script-editor support in the properties panel (which it would not need anyway) — all other standard task types (Manual/Script Task) use bpmn-js's built-in standard palette/context-pad behavior, with no SpiffWorkflow specifics in the UI.
- Should `bpmn-js-spiffworkflow` be actively maintained on npm again in the future and its license situation clarified, a retrofit would be possible but not strictly necessary — the current custom provider already fully covers the actual need.
