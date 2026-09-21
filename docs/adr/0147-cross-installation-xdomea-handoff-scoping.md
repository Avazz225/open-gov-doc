# 0147 — Cross-Installation XDOMEA/XJustiz Handoff: Scoping (transport + task-participation meaning)

**Status:** accepted
**Context:** P37-S1 (Post-Roadmap Phase 37, concept 7.4/14.2) — a **scoping-only** session, explicitly no
implementation commitment (see `IMPLEMENTATION_PLAN.md` "Phase 37"). Picks up the gap
[ADR 0126](0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md) deliberately deferred:
"cross-tenant/cross-authority workflow participation via xdomea" (`docs/egov-feature-gap-analysis.md`,
"Explicitly not prioritized" section), named there as depending entirely on general XDOMEA/XJustiz support
(gap #12, closed across Phase 31/34) being built first. That foundation now exists — this session answers
the two questions ADR 0126 itself left open before a build session could be sensibly planned: (a) how
`federation-hub-service`'s transport could carry a binary XDOMEA/XJustiz package without breaking its
existing trust/encryption model ([ADR 0028](0028-federation-hub-trust-and-encryption-model.md)/
[ADR 0039](0039-federation-trust-hardening-request-signing-over-mtls.md)), and (b) what "workflow
participation across installation boundaries" concretely means.

## Decision

**(a) Transport is already binary-capable with zero protocol change — recommend base64-in-envelope, not a
new payload channel.** `workflow_service.federation_crypto.encrypt_for(public_key_pem, payload: dict)`
JSON-serializes `payload`, then AES-256-GCM/RSA-OAEP-encrypts the resulting bytes; the outer
`federation_hub_service.schemas.HandoverCreate.encrypted_payload` is already a plain, unbounded `str` —
fully opaque to the hub (ADR 0028's whole point: "the hub itself cannot inspect the content"). Carrying an
XDOMEA/XJustiz ZIP therefore needs **no schema change on the hub side at all**: the sending installation
places the ZIP's bytes, base64-encoded, under a new dict key (e.g. `payload["package_base64"]`) before
calling `encrypt_for()`; the receiving installation reads the same key back out after `decrypt_with()`. No
new hub endpoint, no multipart support anywhere in the chain, no change to either trust ADR — signing and
encryption are already payload-content-agnostic.

**(b) "Workflow participation across installation boundaries" splits into two genuinely different
capabilities, only one of which is buildable at all — recommend building only that one.**

1. **DMS-to-DMS automatic package handoff** (buildable, recommended for a later phase): a new
   `taskType=federated` variant (or a reserved `process_type` convention recognized specially by
   `POST /federation/inbound`, see "Rationale") where the task's payload — instead of arbitrary SpiffWorkflow
   process data destined for a BPMN process on the far side — carries a base64 XDOMEA/XJustiz package built
   by `archival-service`'s already-existing general-export functions
   (`build_abgabe_message_for_case`/`_for_document`, [ADR 0127](0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)),
   and the **receiving** installation's `workflow-service` calls `archival-service`'s already-existing
   general-import path (`POST /xdomea/import`, [ADR 0128](0128-xdomea-import-existing-or-new-case-required-process-definition.md))
   automatically instead of a human downloading/uploading the file by hand. This turns ADR 0126's manual
   download/upload flow into an automatic one **between two installations of this DMS software** — a real,
   incremental improvement, not a new standard, protocol, or trust model.
2. **Genuine task delegation into a truly foreign (non-DMS) case-management system** — **not buildable**,
   and not a gap this project can close by building anything at all: a foreign system cannot execute our
   SpiffWorkflow process data (the existing `taskType=federated` mechanism only works because *both* sides
   run this exact software and share a `federation_process_type_map` convention, see
   `docs/services/workflow-service.md` "Federation"), and XDOMEA/XJustiz are document/case interchange
   *formats*, not task-orchestration *protocols* — no shared "please run this step and hand control back"
   semantics exists for them to carry. For a foreign authority, a **file handoff is not a lesser stand-in
   for "real" participation — it is the only form participation can structurally take**, and ADR 0126 already
   delivers it (download/upload via the existing UI). This half of the originally named gap is therefore
   **already closed**, not still open, and should be documented as such rather than carried forward.

**No implementation in this session.** This ADR records the recommendation; a future build session (not yet
scheduled) would implement option (1) above, informed by this scoping.

## Rationale

- **Why base64-in-envelope over any alternative transport (e.g. a new binary hub endpoint, or a
  storage-service-mediated download-link handoff)**: a new binary endpoint on `federation-hub-service` would
  require the hub to accept/relay non-JSON bodies while still not being able to inspect them (no actual
  benefit over base64-in-JSON, since the ~33% size overhead of base64 is the only cost and the hub treats the
  string as opaque either way) — pure added surface for no gain. A storage-service-mediated link (upload the
  package somewhere reachable, hand over only a URL) would reintroduce exactly the shared-storage assumption
  Concept 7.4 explicitly rejects for federation ("operator model" — a foreign hub/installation pair may share
  nothing but the hub's mediation) and would leak metadata (the URL's existence, timing of retrieval) outside
  the encrypted envelope. Base64-in-envelope keeps the "hub cannot inspect content" property completely
  intact and needs zero new infrastructure.
- **A real, non-hypothetical scale risk found while confirming (a) is actually workable**: `federation-hub-service`'s
  own "Retry & Backoff" design ([ADR 0081](0081-federation-hub-handover-retry-backoff-pending-retry.md))
  deliberately keeps the encrypted payload **only in the hub's process memory** (`app.state.pending_handover_payloads`)
  for the duration of any retry window — a design built around small, JSON-shaped BPMN task data. A multi-
  document case export can plausibly be single-digit megabytes before base64 inflation; several such handovers
  in-flight simultaneously (multiple installations, multiple retries) is a real memory-pressure question this
  ADR flags but does **not** resolve — a genuinely close analog to the connection-pool exhaustion finding from
  Post-Roadmap Phase 36 Session 3 (`reporting-service`/`query-service`'s shared unbounded-fan-out `filtering.py`
  pattern, see `docs/services/reporting-service.md` "Open Points"): a mechanism whose original design assumption
  (small payloads) quietly stops holding once a genuinely large payload class is routed through it. A future
  build session should re-examine `max_handover_delivery_attempts`/payload size in combination, not assume the
  existing retry design scales unchanged. Both installations' `httpx.AsyncClient(timeout=15.0)` calls
  (`federation_client.py`) would also need raising for realistically-sized packages — a small, mechanical
  change, not a design question.
- **Why the DMS-to-DMS case is worth building but the foreign-system case is not**: the entire value of
  "automatic" over "manual download/upload" is removing a human from a repeatable, already-standardized round
  trip — that value only exists when the receiving side can be told, in a machine-checkable way, what to do
  with the package (here: "run the existing XDOMEA import path"). A genuinely foreign system's internal
  behavior is unknowable to us by definition; automating "then what happens on their end" is not something
  any amount of engineering on our side can achieve without their side implementing a matching protocol,
  which the XDOMEA/XJustiz *format* standards do not define. Conflating the two under one "cross-tenant
  workflow participation" heading (as the original gap-analysis wording did) obscures that only one of them
  is actually a gap.
- **Why extend `taskType=federated` rather than invent a third federation primitive**: the dispatch
  machinery (automatic sending on task-ready, `federation_client.py`, the hub's signed/encrypted transport,
  `FederationTask` bookkeeping for idempotent delivery) is already fully generic over payload *content* — only
  the *meaning* of the payload differs (SpiffWorkflow process data today, an XDOMEA package tomorrow). A
  reserved `process_type` value (e.g. `"xdomea.case_handoff"`) recognized by `POST /federation/inbound`
  before it falls through to the generic "start a BPMN instance via `federation_process_type_map`" behavior
  is the smallest change that reuses every existing trust/retry/idempotency mechanism unchanged — see
  `docs/services/workflow-service.md` "Federation" for the exact current dispatch/receive code paths this
  would extend.
- **Why not resolve the archival-service import endpoint's multipart-vs-JSON mismatch here**: `POST
  /xdomea/import` today takes a multipart file upload (human-driven UI flow, ADR 0128); an automatic
  DMS-to-DMS handoff would call it (or a JSON-bodied sibling) from `workflow-service`'s own inbound handler,
  not from a browser. Which of "add a JSON-body import variant" vs. "have `workflow-service` synthesize a
  multipart request internally" is the better shape is an implementation detail for the actual build session,
  not a scoping question — flagged here only so it isn't rediscovered as a surprise then.

## Consequences

- **`docs/egov-feature-gap-analysis.md`'s "Cross-tenant/cross-authority workflow participation via xdomea"
  entry is split by this ADR**: the foreign-system half is reclassified from "deferred, depends on gap #12"
  to **"already closed by ADR 0126's download/upload flow — no further action possible or planned"**; the
  DMS-to-DMS half becomes a **new, concretely scoped candidate** for a future phase (not yet scheduled, no
  session number assigned by this ADR).
- **No code diff in this session** — `federation-hub-service`, `workflow-service`, `archival-service` are
  all unchanged. `PROGRESS.md` marks this session explicitly as scoping-only, no feature, per Phase 37's own
  Definition of Done.
- **A future build session inherits a fully bounded starting point**: envelope encoding decided (base64 in
  the existing JSON payload dict), dispatch mechanism decided (extend `taskType=federated`/`POST
  /federation/inbound` via a reserved `process_type`, not a new primitive), and one open engineering question
  correctly flagged rather than silently assumed away (payload-size interaction with the hub's in-memory
  retry cache and both sides' HTTP client timeouts) — avoiding the "premature to design before the foundation
  exists" trap ADR 0126 itself named, now that the foundation (general XDOMEA/XJustiz export+import,
  Phase 31/34) is actually in place.
- **This project's federation trust model (ADR 0028/0039) is confirmed to need no changes** for this future
  work — a genuinely reassuring scoping outcome: the two-and-a-half-year-old (in-project-time) trust design
  was built generically enough to carry a payload shape (large binary documents) never anticipated at the
  time it was designed.

**Closed (Phase 43 Session 1, [ADR 0159](0159-dms-to-dms-xdomea-handoff-implementation.md))**: the
DMS-to-DMS build this ADR scoped was implemented exactly along the bounded starting point named above —
base64-in-envelope transport, `taskType=federated`/reserved `process_type` dispatch, no trust-model
changes. ADR 0159's own Consequences state this closes ADR 0126's originally-deferred DMS-to-DMS gap.
