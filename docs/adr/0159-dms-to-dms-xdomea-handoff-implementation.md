# 0159 — DMS-to-DMS XDOMEA handoff: implementation

**Status:** accepted (P43-S1, see Phase 38+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 43 Session 1 (build session), implements the scoping already recorded in
[ADR 0147](0147-cross-installation-xdomea-handoff-scoping.md)/P37-S1, affects `workflow-service`,
`federation-hub-service`

## Decision

The automatic DMS-to-DMS case handoff ADR 0147 scoped but explicitly did not build is now built,
exactly along the lines that ADR 0147 recommended:

1. **A reserved `process_type` value, `"xdomea.case_handoff"`**, on an ordinary `taskType=federated`
   task. `_dispatch_outbound_federated_task` (sender side) special-cases this value: instead of
   encrypting raw SpiffWorkflow `task.data`, it reads `task.data["case_id"]`, calls a new
   `ArchivalServiceClient.export_case` (a thin HTTP client against `archival-service`'s already-existing
   `POST /xdomea/export/cases/{id}`, ADR 0127), and encrypts `{"package_base64": ..., "case_id": ...}`
   instead. Everything else about dispatch — handover creation, `FederationTask` bookkeeping, leaving
   the task pending rather than completing it — is unchanged.
2. **`POST /federation/inbound` intercepts this reserved value before the generic
   `federation_process_type_map` lookup.** A new `_handle_inbound_xdomea_handoff` decrypts the payload,
   base64-decodes `package_base64`, and calls a new `ArchivalServiceClient.import_package` — a
   synthesized multipart request against `archival-service`'s already-existing `POST /xdomea/import`
   (ADR 0128), using the receiving installation's own configured `xdomea_handoff_target_folder_id`/
   `xdomea_handoff_process_definition_id`. No local `ProcessInstance` is started for this path at all.
3. **The sending installation's task is completed via the EXISTING, completely unmodified
   `send_result`/`POST /federation/inbound-result` mechanism** — `_handle_inbound_xdomea_handoff` sends
   the import outcome (success or failure) back through the hub to the origin installation, exactly the
   way `_dispatch_federated_return_task` already does for the generic `federated_return` case.
   `POST /federation/inbound-result` needed no code change to support this.
4. **`archival-service` needed no changes at all** — both endpoints it already exposed for the
   human-driven manual handoff (ADR 0126) are reused unchanged; the new `ArchivalServiceClient` in
   `workflow-service` calls them exactly as a browser-driven upload would, just with already-decoded
   bytes instead of a file picker (ADR 0147's own flagged "lower-risk" choice over adding a JSON-body
   import variant).
5. **`FederationTask.process_instance_id` is now nullable** (idempotent migration) — a reserved-type
   inbound row genuinely has no `ProcessInstance` to reference.
6. **Two mechanical timeout fixes and one new size-limit safeguard** address ADR 0147's own flagged
   payload-size risk: `workflow_service.settings.federation_hub_request_timeout_seconds` (60s, was
   hardcoded 15s) and `federation_hub_service.settings.hub_delivery_timeout_seconds` (60s, was hardcoded
   15s) on the two `httpx.AsyncClient`s that carry the (base64-inflated) payload; a new
   `max_handover_payload_chars` (default ~100M) rejects an oversized `encrypted_payload`/
   `encrypted_result` with `413` before any target/version lookup.

## Rationale

- **Verified the exact clone-cache mechanism before writing production code** (see ADR 0158's own
  precedent for this discipline in a different service this same phase) — not directly relevant here
  since this session is pure HTTP/crypto plumbing, no PDF object-graph work, but the same "prove the
  design empirically first" approach was applied to the trickiest single piece: confirming, via the
  actual running services, that `_handle_inbound_xdomea_handoff`'s confirmation reaches
  `POST /federation/inbound-result` and genuinely completes the originally-pending task — not assumed
  from reading the generic path's code alone.
- **Why the confirmation reuses `send_result` rather than a new callback**: the alternative — completing
  the sending task immediately on successful dispatch, fire-and-forget — was rejected because it would
  silently misrepresent the actual outcome: `create_handover` only proves the payload reached the HUB,
  not that the receiving installation actually imported it successfully. Since
  `POST /federation/inbound-result` was ALREADY a completely generic, content-agnostic callback (it just
  decrypts whatever dict `encrypted_result` contains and hands it to `complete_task` as the task's new
  data), reusing it exactly needed zero changes there and gives the sending BPMN process a genuine,
  accurate completion payload (`status`/`case_id`/`document_ids`/etc., or `status: "failed"` plus an
  error) to branch on if it wants to.
- **Why `archival-service` needed no changes, confirmed rather than assumed**: ADR 0147 itself flagged
  the multipart-vs-JSON mismatch as an open implementation decision. Checking the actual
  `general_import.import_abgabe_package` signature (`zip_bytes: bytes`, not `UploadFile`) confirmed the
  HTTP layer's multipart requirement is a thin wrapper around an already-bytes-based function — `httpx`'s
  own `files={...}` support lets `workflow-service` synthesize an equivalent request without needing a
  new JSON-bodied sibling endpoint, exactly the lower-risk path ADR 0147 named.
- **A real, pre-existing `archival-service` gap was found only by this session's own live verification,
  not by reading the code**: the first live-verification attempt (a real, freshly created, still-OPEN
  case with a real document reference) produced a case-created-but-zero-documents result. Direct
  inspection of `general_export.build_case_export_package` found it filters on
  `snapshot_version_number is not None` — a field that only gets set when a case CLOSES, contradicting
  ADR 0127's own stated "the case does NOT need to be closed first." Closing the case (completing its
  BPMN task) and re-running the identical export immediately produced the correct 2-file package. This
  is a genuine, separate, PRE-EXISTING bug in a different session's code (Phase 31/34), not something
  this session introduced — deliberately NOT fixed here (out of scope: a session about transport
  mechanics is not the place to redesign export-eligibility semantics), but documented in both
  `docs/services/archival-service.md` and `docs/services/workflow-service.md` "Open Points" so it isn't
  lost. Confirmed the finding was real by reproducing it twice (open case → empty package; same case,
  closed → package with the document) rather than trusting a single observation.
- **Why a bounded size ceiling (413) rather than a full retry-storage redesign for the hub's
  memory-pressure risk**: ADR 0147 flagged this as a real, unresolved question but explicitly deferred
  its resolution to "a future build session," using conditional language ("should re-examine... in
  combination"), not a hard requirement to solve fully now. A full redesign (external storage for
  `pending_handover_payloads`/`pending_handover_result_payloads`) is a separate, larger architectural
  change disproportionate to this session's actual scope (building the handoff feature itself). A
  configurable, explicit ceiling converts "unbounded, silent risk" into "bounded, immediately visible
  failure" — a real, if partial, improvement — while leaving the heavier redesign for if/when actual
  concurrent handoff volume ever makes it necessary. This project's own established pattern favors an
  honestly-documented partial mitigation over silently expanding scope to a full fix.

## Consequences

- **The DMS-to-DMS half of the originally-named "cross-tenant workflow participation" gap (concept
  7.4/14.2) is now fully closed**, not just scoped. The foreign-system half remains closed-as-in-not-
  buildable per ADR 0147's own finding (unchanged).
- ~~**A real, pre-existing `archival-service` limitation is now documented** (open-case export silently
  drops document references) — a genuine, separate future-session candidate, not blocking this feature
  for its most natural use case (handing off a case at the point a local process closes/finalizes it,
  which is exactly the live-verification scenario that surfaced this).~~ — **closed by ADR 0170**
  (`case-export-open-case-document-inclusion.md`, Phase 52 Session 4): `general_export.
  _resolve_export_version()` falls back to `current_version_number` for open cases.
- **Hub memory-pressure risk is bounded, not eliminated** — see "Rationale" above. Both installations'
  outbound HTTP timeouts are raised from the original small-payload-era 15s default to 60s.
- **No archival-service code changed** — a genuine zero-footprint reuse of Phase 31/34's existing general
  export/import endpoints, confirming ADR 0126's original design (splitting general exchange from the
  disposal pipeline, keeping the export/import functions transport-agnostic) paid off exactly as
  intended when a second transport (federation, not just manual download/upload) needed to reuse them.
- **Tests**: `workflow-service` 215 tests (previously 208, +7, new `test_xdomea_handoff.py`) — see
  `docs/services/workflow-service.md` "Tests" for the exact breakdown (outbound dispatch guards against
  the real hub, inbound logic against a boundary-patched `archival_client`/`federation_client`, since a
  full round-trip callback needs a real socket `TestClient` cannot provide). `federation-hub-service` 73
  tests (previously 69, +4, new size-limit rejection/allow-at-limit tests for both `POST /handovers` and
  `POST /handovers/{id}/result`). Live-verified end to end against the rebuilt, restarted real stack: a
  genuine self-loopback handoff (an installation registered with, and handing off to, itself — the
  established pattern this project already uses for federation live verification) moved a real closed
  case with a real document reference through the real hub into a brand-new case on the "receiving" side,
  byte-identical content confirmed, with the original task/instance completing automatically via the
  reused confirmation mechanism.
