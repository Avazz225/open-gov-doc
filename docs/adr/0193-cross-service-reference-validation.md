# 0193 — Cross-service reference/type validation: `reference_target`, DMN delete-in-use check, `targetProcessType` design-time warning

**Status:** accepted
**Context:** P64-S1 (Phase 64, the only session of the phase, concludes the Phase 63+ gap-closure round),
affects `object-type-service`, `document-service`, `folder-service`, `workflow-service`, `process-designer`

## Decision

Closes the three remaining instances of the "declared cross-service reference/type stored but never
checked against its actual target" pattern (the fourth instance, `federation-hub-service`'s handover
`process_type`, closed in P63-S2/ADR 0191).

**(a) `type: "reference"` attributes get a real, scoped existence check.** The plan's own premise —
"reuse existing validation" — was wrong: no value-existence-checking mechanism existed anywhere.
`dms_constraint_engine`'s `reference` branch has only ever checked shape (non-empty string), by design
(its own docstring: "a generic reference type → service resolution doesn't yet exist"), and the engine
stays a pure, stateless library with no DB/HTTP access (ADR 0003) — it structurally cannot perform an
existence check itself. Building the missing piece meant first answering an under-specified question the
plan didn't address: existence against *what*? A new optional `reference_target` key
(`"document"`/`"folder"`) on an attribute definition — `object-type-service` format-validates it
(`_validate_reference_targets`, new `InvalidFieldError` case, same shape as `_validate_status_transitions`)
at object-type save time; `document-service`/`folder-service` (the only two callers, the only two
services holding referenceable instance data) perform the actual existence check themselves, in
`_check_reference_attributes`/`_validate_against_object_type` respectively, right after `object_type_client.
validate()` confirms the shape. `"document"` resolves via a same-service DB lookup
(`repository.document_exists`); `"folder"` via the existing cross-service client
(`folder_client.get()`/`document_client.get()` on the other side) — the exact same clients both services
already use for parent-folder/subtree existence checks elsewhere.

**(b) `delete_dmn_definition` gets a real "in use" check**, mirroring `delete_process_definition`'s
existing FK-based pattern but with a materially different scope, since the thing that must not break is
not a DB row but a *BPMN text reference*: `spiff_adapter.extract_decision_refs()` (new, namespace-aware
`camunda:decisionRef` extraction via the standard library's `ElementTree`) scans every LATEST-per-family
`ProcessDefinition.bpmn_xml` for the DMN's `decision_id`; the delete is blocked (`DmnDefinitionInUseError`,
`409`, same convention as `ProcessDefinitionInUseError`) only when the version being deleted is itself the
latest of its family AND that `decision_id` is referenced — an already-superseded version is never loaded
by `list_latest_dmn_xml()` (see its own docstring), so it cannot be what a `businessRuleTask` actually
resolves against, and stays deletable regardless of any reference.

**(c) `process-designer`'s `targetProcessType` gets a client-side, non-blocking warning**, not a hard
save-time rejection. The plan assumed a new fetch/endpoint would be needed; investigation found the data
was already flowing through the existing pipe unfiltered — `federation-hub-service`'s `GET /installations`
has carried `supported_process_types` per installation since P63-S2/ADR 0191, `workflow-service`'s
`/federation/installations` proxy already forwards it, only the TypeScript types on both hops narrowed it
away. Widening `FederationInstallationSummary`/`FederationInstallation` to include the field needed no
backend change. `TargetProcessTypeField` gained a `validate` function
(`validateTargetProcessType`, extracted as a standalone, independently unit-tested pure function) using
`@bpmn-io/properties-panel`'s own `TextFieldEntry` `validate` prop — a WARNING only (inline message,
cannot block saving the diagram), matching the plan's own offered choice and consistent with the fact
that real enforcement already happens at the hub (ADR 0191): the design-time check is a convenience,
catching the mismatch earlier, not a second source of truth. Empty `supported_process_types` = no
restriction declared, the exact convention the hub itself already established when it started actually
enforcing this field.

**A real, pre-existing bug found live during (c)'s own verification, unrelated to this session's actual
change:** `TargetProcessTypeField`'s `TextFieldEntry({...})` call never passed a `debounce` prop.
`@bpmn-io/properties-panel`'s `Textfield` unconditionally calls `useDebounce(onInput, debounce)`, which
calls `debounce` itself as a function with no fallback — every real caller of `TextFieldEntry` (e.g.
`bpmn-js-properties-panel`'s own built-in `CalledElement` binding) resolves this via the `debounceInput`
service `BpmnPropertiesPanelModule` already registers internally
(`__depends__: [Commands, DebounceInputModule, FeelPopupModule]`); this field simply never injected it.
The result: the ENTIRE `Ziel-Prozesstyp` field has silently crashed on first mount since this feature was
built (P6-S9) — not a regression from adding `validate`, `validate` merely gave a reason to actually open
this specific field in a real browser for the first time. No prior e2e coverage of the federated-step
properties panel existed anywhere in this app (`designer.spec.ts` only exercises generic task
creation/save; this codebase's own unit tests mock `@bpmn-io/properties-panel` out entirely, which is
exactly why a real render bug like this was invisible to them). Fixed with one line:
`const debounce = useService("debounceInput")`, passed through to `TextFieldEntry`.

## Rationale

- **Scoping `reference_target` to `"document"`/`"folder"` only, not a generic "reference type → service"
  resolution**: the two are the only instance-holding services that call `dms_constraint_engine` at all
  (document-service/folder-service); a generic cross-service resolution mechanism (as the engine's own
  pre-existing docstring already named as future, unscoped work) is a materially larger design problem —
  which OTHER service, which lookup shape, how to keep it from becoming N+1 across every possible target —
  not warranted by any concrete use case found anywhere in this codebase (a grep across every
  `object-type-service`/`document-service`/`folder-service` test file found ZERO existing usage of
  `type: "reference"` anywhere before this session).
- **`reference_target` optional, not required, on a `reference`-type attribute**: preserves the
  pre-existing, still-correct format-only behavior for anyone not ready to declare a target — adding a new
  REQUIRED field to an existing, already-shipped attribute type would be a breaking schema change for no
  reason; the existence check is additive.
- **DMN delete-protection scoped to "latest version of its family", not "any version ever created"**:
  mirrors the exact scope `list_latest_dmn_xml()` itself already uses when loading DMN definitions before
  a BPMN parse — a check broader than that scope would block legitimate cleanup of long-superseded
  versions for no safety benefit, since they were never reachable by a `businessRuleTask` resolution in
  the first place.
- **`extract_decision_refs` via plain `ElementTree`, not a full `DmsBpmnParser` parse**: this check only
  needs to know WHICH `decisionRef` ids are textually present, not build an executable SpiffWorkflow task
  spec — a full parse would be strictly more expensive for no additional correctness, and would itself
  require DMN definitions to already be loaded (circular for a delete-time check).
- **`targetProcessType` as a warning, never a hard rejection**: real enforcement already exists at the hub
  (ADR 0191) — a resolvable-later mismatch (the target installation declares its catalog only AFTER this
  diagram is drawn, or a diagram drawn before the hub round-trip is even reachable) must not lock a BPMN
  author out of saving; the plan's own text explicitly offered "warning vs. hard rejection" as the
  session's call, and the hub-side precedent (`ADR 0191`'s "empty = unrestricted" convention) already
  established the right default risk posture for this exact kind of check.
- **The `debounce` fix is scoped to fixing the ONE broken call site, not auditing every `TextFieldEntry`
  usage in this codebase** — there is exactly one (`TargetProcessTypeField`), confirmed by grep across
  every component file in `process-designer`; `SignatureTaskPropertiesProvider`'s own text-like fields use
  `CheckboxEntry`/`SelectEntry` only, which don't hit this code path (no internal `useState`/`useDebounce`
  in those Entry types' implementations).

## Consequences

- `document-service`/`folder-service` now perform ONE additional lookup (own-DB or cross-service HTTP,
  depending on `reference_target`) per reference-typed attribute value actually present on a document/
  folder create or attribute-changing update — an accepted cost, same trade-off this project already
  makes for `parent_id`/`folder_id` existence checks on the same code paths.
- No admin-UI form support for `reference_target` was added in this session (API-only, matching this
  project's established "backend-first, UI follows later if a real need appears" precedent already used
  for `status_transitions`) — `admin-ui`'s `ObjectTypeEditor.tsx` still offers `"reference"` as an
  attribute type but does not expose a target picker; an admin configuring one today must set
  `reference_target` via direct API access.
- `DELETE /dmn-definitions/{id}` can now return `409` where it previously always succeeded — a real,
  intentional behavior change (closing a gap this project's own docs already named as a deliberate
  limitation) for any installation with a `businessRuleTask` referencing the DMN being deleted.
- `process-designer`'s federated-step properties panel is now genuinely usable end to end for the first
  time — previously the `Ziel-Prozesstyp` field silently crashed on render, an invisible, pre-existing gap
  in a feature shipped since P6-S9, found only because this session did real browser verification of a
  panel area no prior session had actually opened in a browser.
- New tests: `object-type-service` 109/109 (+5), `document-service` 412/412 (+5), `folder-service` 167/167
  (+3), `workflow-service` 227/227 (+6: 2 repository, 1 API, 3 `spiff_adapter` unit), `process-designer`
  58/58 (+11 unit tests for `validateTargetProcessType`). `ruff`/`eslint`/`tsc` clean across all five
  services/apps (pre-existing, unrelated repo-wide `ogdoc_addin.py`/`analysis.ipynb` ruff-format findings
  confirmed out of scope again, same as every prior session this round).
- **Live-verified against the real running stack**, all three sub-parts: (a) a real reference-typed
  attribute on both a document and a folder object type, a nonexistent target rejected (`422`), a real
  target (an existing document id / the seeded `"root"` folder) accepted; (b) a real DMN definition +
  referencing process definition uploaded, delete blocked (`409`) while referenced, succeeds once the
  process definition is removed; (c) **a real Playwright browser session** against `process-designer`
  (temporary spec, removed afterward, same convention as every prior UI verification in this project) —
  logged in, placed a Manual Task, enabled the federated step, selected a real installation from the dev
  stack's own address book (`supported_process_types: ["dms.contact-directory.v1"]`), typed an undeclared
  process type (inline warning appeared), then the declared one (warning cleared) — this run is what
  surfaced the pre-existing `debounce` crash bug above, fixed in the same session before this verification
  could pass.
