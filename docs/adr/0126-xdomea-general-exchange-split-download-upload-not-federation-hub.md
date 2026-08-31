# 0126 — General xdomea/XJustiz exchange: three-way session split, download/upload transport (not federation-hub)

**Status:** accepted (P31-S13, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 13 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #12), affects `archival-service`
(new capability, not modified in place — see "Decision"), `document-service`, `case-service`, `user-ui`

## Decision

`IMPLEMENTATION_PLAN.md`'s own P31-S13 line and the gap analysis both flagged this session as too large for
one pass — "likely its own multi-session sub-effort." A research agent read the current
`archival-service`/`xdomea.py` implementation (export-only, XDOMEA 4.0.0, scoped to exactly one message
type, `Aussonderung.Aussonderung.0503`, [ADR 0029](0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)),
the `federation-hub-service` trust/transport model ([ADR 0028](0028-federation-hub-trust-and-encryption-model.md)),
this project's existing typed-reference/classification/redaction patterns
([ADR 0114](0114-per-document-classification-level.md)/[ADR 0115](0115-document-redaction-genuine-content-removal.md)),
and the external XDOMEA 4.0.0/XJustiz standards (via web research — XJustiz's official catalog spans 27
modules and 144 message types, roughly two orders of magnitude larger than XDOMEA's one implemented
message). Both findings were put to the user directly via `AskUserQuestion`:

1. **Session split**: three independently-shippable sessions — **P31-S13a** XDOMEA general export,
   **P31-S13b** XDOMEA import, **P31-S13c** XJustiz — chosen over combining export+import into one session.
   Isolating XJustiz into its own session was the key driver: its message catalog is disproportionately
   larger than XDOMEA's, and blending it with XDOMEA work would obscure that scale difference rather than
   let it be scoped deliberately at its own session's start (same `PN-S0`-style research-first pattern
   already used elsewhere in this project).
2. **Transport scope**: "inter-agency handoff" in this phase means a downloadable/uploadable package via
   the existing UI (same shape as the already-existing disposal export: build a ZIP, store it, offer it for
   download), **not** new automatic cross-installation delivery through `federation-hub-service`. Wiring
   `federation-hub-service`'s signed-envelope transport to also carry a binary XDOMEA/XJustiz payload — and
   thereby closing the separately-tracked "cross-tenant workflow participation via xdomea" gap
   (`docs/egov-feature-gap-analysis.md`, "Not carried forward or explicitly deferred" section) — is
   deliberately deferred to a later, separate effort.

## Rationale

- **XJustiz's scale genuinely warrants its own session, not a shared one with XDOMEA**: XDOMEA 4.0.0 today
  has exactly one implemented message (`0503`); the standard itself is organized into small message groups
  (Aktenplan, Aussonderung, Geschäftsgang, Abgabe, Übermittlung). XJustiz, by contrast, is a genuinely
  different scale of standard — 27 modules, 144 message types, 43 code lists across six domains (civil/
  family, register, enforcement, administration, cross-cutting, criminal). A combined session would either
  shortchange XJustiz's real scoping needs or balloon far past what "one session" should mean in this
  project's roadmap.
- **XDOMEA export and import split rather than combined**: even though both reuse the same vendored-schema
  infrastructure ([ADR 0029](0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)'s `lxml`-against-
  official-XSD approach), they are functionally distinct capabilities with different call sites (export:
  triggered from an existing document/case; import: a new inbound entry point creating documents/case
  references from external data) — splitting keeps each session's own Definition of Done meaningfully
  checkable, consistent with the P31-S12a/b/c precedent
  ([ADR 0123](0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md)).
- **Download/upload, not federation-hub delivery, matches the ALREADY EXISTING precedent in this exact
  codebase**: the current disposal-pipeline XDOMEA export already works this way (`archival-service` builds
  the ZIP, uploads it to `storage-service`, an operator retrieves it) — generalizing that same shape to
  arbitrary documents/cases is a natural, bounded extension. `federation-hub-service`'s transport, in
  contrast, is metadata-only-mediated and was purpose-built for BPMN task handover
  (`taskType=federated`/`federated_return`, [ADR 0028](0028-federation-hub-trust-and-encryption-model.md)) —
  its envelope (`encrypt_for()`, a JSON dict payload) was never designed for binary ZIP transport and would
  need real adaptation, on top of the format work this session is actually about.
- **Real-world XDOMEA/XJustiz exchange is routinely download/upload-based in practice anyway** (a package
  handed off via a portal, email, or physical medium between authorities that don't share a live network
  connection) — a downloadable package is not a lesser stand-in for "real" inter-agency handoff, it is
  itself a complete, standards-conformant delivery mechanism.
- **Automatic cross-installation delivery is explicitly a separate, larger, dependent effort** — the gap
  analysis already tracks it as its own item ("Cross-tenant/cross-authority workflow participation via
  xdomea... depends entirely on gap #12 being built"), correctly identifying it as downstream of, not part
  of, the format work this session covers.

## Consequences

- `IMPLEMENTATION_PLAN.md`'s P31-S13 line is replaced by three rows: **P31-S13a** (XDOMEA general export),
  **P31-S13b** (XDOMEA import), **P31-S13c** (XJustiz) — same pattern as the P31-S12 split.
- Each of the three sub-sessions gets its own Definition of Done and, where warranted, its own ADR for
  non-trivial decisions made during implementation (e.g. exact XJustiz message type chosen for P31-S13c,
  researched at that session's own start rather than pre-decided here).
- **Automatic cross-installation delivery via `federation-hub-service` remains explicitly out of scope**
  for all three P31-S13 sub-sessions — a plausible, separate future phase if that gap is ever prioritized,
  building on both this work (the package format) and `federation-hub-service`'s existing trust model
  (the transport), but not attempted here.
- `archival-service`'s existing disposal-only export path (P7-S3b) is **not modified in place** by
  P31-S13a — the plan is to generalize `xdomea.py`'s message-building logic so it can be invoked for an
  arbitrary document/case, not to change the disposal pipeline's own existing call site or message shape.
