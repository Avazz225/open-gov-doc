# 0021 — bpmn.io license (watermark) accepted for the Process Designer

**Status:** accepted
**Context:** Roadmap forward-planning after P6-S2 (rest of Phase 6 researched ahead of time so that future sessions can start faster). Concept 1a/8 explicitly names `bpmn-js` as an example for the BPMN 2.0 modeling component of the Process Designer (now P6-S8, formerly P6-S6), with `bpmn-js-spiffworkflow` (sartography) adding the SpiffWorkflow-specific properties (script editor, properties panel). During the license review it was found that `bpmn-js`/`dmn-js`/`form-js`/`cmmn-js` (the bpmn.io toolkits) are licensed under their own **"bpmn.io License"**, not MIT/Apache-2.0. `bpmn-js-spiffworkflow` itself is MIT-licensed, but has `bpmn-js` as a core dependency and thereby effectively inherits its license condition.

## Decision

`bpmn-js`/`bpmn-js-spiffworkflow` are used, as proposed in the concept, as an unmodified dependency for the Process Designer (P6-S8), including the fixed, hardcoded watermark (link to bpmn.io) mandated by the bpmn.io license on every rendered BPMN diagram.

## Rationale

- **The bpmn.io license allows unrestricted commercial use, modification, and redistribution** — the only special condition compared to a standard MIT license is: the code that renders the bpmn.io watermark must not be removed or altered, and the watermark must remain fully visible in every embedding. No paid option was found to remove the watermark (unlike some similarly licensed toolkits from other vendors).
- **Same pattern as ADR 0018** (SpiffWorkflow, LGPLv3): a library explicitly proposed in the concept, and a quasi-standard in the BPMN ecosystem, is accepted with its license terms unchanged, rather than pursuing a custom build or an unproven alternative.
- **Building a BPMN 2.0 rendering/modeling component in-house** (canvas engine, palette, properties panel, XML import/export, undo/redo) would be many times the effort of a single session (P6-S8) and would be wholly disproportionate to the sole goal of avoiding a watermark — especially since `bpmn-js-spiffworkflow` already brings the SpiffWorkflow integration this project needs, and a replacement would have to rebuild that integration too (the same consideration ADR 0018 already recorded on the engine side).
- This assessment, too, is **not legal advice**, but a pragmatic evaluation for the current internal development/test operation (analogous to ADR 0018).

## Consequences

- Every view of the Process Designer (P6-S8) visibly shows the bpmn.io logo/link — not a technical blocker for internal administrative software, but to be communicated to stakeholders ahead of any potential later third-party distribution/white-label need.
- Should a white-label requirement arise in the future, this decision would need to be revisited (alternative library or in-house build) — no current need, so not pursued ahead of time.
- `bpmn-js-spiffworkflow`'s own MIT license imposes no additional restriction; it merely uses `bpmn-js` as a peer dependency without altering its license terms.

## Addendum (P14-S4): `dmn-js` actually put into operation

This ADR already named `dmn-js` in 2026 (P6-S2 forward-planning) as a precaution, as a bpmn.io toolkit covered by the same license decision - P14-S4 (DMN 1.3 decision tables in the Process Designer, 7.1) has now actually added it as a dependency (`apps/process-designer`, `DmnDesigner.tsx`) and confirmed via a spike, **empirically**: `dmn-js` 17.10.1 uses the same `diagram-js` major version (`^15.23.2`) as the pinned `bpmn-js` 18.22.1 stack; a real `next build`/static-export run as well as a live browser test (decision-table view including hit-policy dropdown, rule rows, import/export) both ran without errors. No fallback to a raw XML editor was needed, no new license decision was required - the same rationale/the same watermark as above apply unchanged.
