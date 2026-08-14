# 0018 — SpiffWorkflow accepted as an LGPLv3 dependency

**Status:** accepted
**Context:** Concept 13 (open point "SpiffWorkflow license"), `IMPLEMENTATION_PLAN.md` explicitly named the license check as a prerequisite before P6-S1 (Workflow Engine foundation). Made as part of a consolidation session for open decisions after the completion of Phase 5b, not within a dedicated P-session.

## Decision

`SpiffWorkflow` (LGPLv3) is accepted as an unmodified Python dependency for the Workflow Engine (P6-S1). No switch to an alternative engine, no delaying P6-S1 pending a formal external legal review.

## Rationale

- **LGPLv3 distinguishes between the library itself and its use as a dependency**: the copyleft obligation (source disclosure on distribution) does not extend to the overall code of the consuming system when used unmodified as a library dependency — it only applies if `SpiffWorkflow` itself is modified and that modification is distributed. This project incorporates `SpiffWorkflow` unmodified via `uv`/PyPI (no fork, no patch), exactly the case that LGPL exempts from the stronger GPL copyleft effect.
- **No structural difference from dependencies this repo already has**: the project already uses various open-source libraries under different licenses (Apache-2.0, MIT, BSD) as pure dependencies, without their license terms bleeding into its own code — SpiffWorkflow as a dependency does not differ in category from a licensing standpoint, only in the specific license family (copyleft instead of permissive).
- **No replacement of comparable maturity available**: `bpmn-js-spiffworkflow` (the frontend counterpart for P6-S8, formerly P6-S6) is built specifically for SpiffWorkflow — switching the engine would also have affected the Process Designer approach (`bpmn-js` itself, incidentally, has its own license quirk, see [ADR 0021](0021-bpmn-io-license-watermark.md)). No other Python BPMN engine with comparable feature scope (manual/automatic tasks, timer/boundary events for P6-S2, signature task-type extensibility for P6-S7, formerly P6-S5) was identified that would have a more permissive license.
- **This assessment is not legal advice**: it is a technical/pragmatic evaluation within the scope of project development, not a substitute for a formal legal review. Should the system ever be distributed externally in the future (to third parties, as a closed-source product), this assessment must be revisited before such a step — for the current internal development/test operation, it is considered sufficient to no longer block P6-S1.

## Consequences

- P6-S1 can start without further preconditions — the "LGPLv3 license check first" gate previously stated in `IMPLEMENTATION_PLAN.md` is dropped.
- Should SpiffWorkflow itself ever need to be modified (e.g. a patch for a missing BPMN task type), the LGPL copyleft obligation applies to exactly that modification — this case is not currently planned but is recorded as a condition of this decision.
- In the event of future third-party distribution of the overall system, this ADR must be marked "provisional, for internal operation" and the license question must be clarified again with actual legal counsel.
