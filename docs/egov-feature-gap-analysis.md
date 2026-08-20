# eGov Feature Gap Analysis

**Purpose:** a structured comparison between this project's current government-focused feature set
(the `packages/egov/` configuration package, ADR 0059/ADR 0060, and the underlying platform
capabilities they build on) and the feature set of a mature, long-established commercial German
government-sector (eGov) document management add-on — referred to throughout this document only as
**"the reference system"**, never by name. The reference material was that product's own release
notes, covering roughly seven years of releases (its first release through its current long-term-support
version).

This document does not evaluate the reference system as a product to compete with feature-for-feature —
many of its ~300 catalogued features are minor UI conveniences, client-parity fixes, or internal
plumbing irrelevant to this project's architecture. Instead, it isolates the subset of capabilities that
are **distinctly valuable for a government-focused DMS** and that this project does not yet have, so they
can be triaged into a concrete session plan (see `IMPLEMENTATION_PLAN.md`, Phase 31).

## Method

Five parallel research passes read the reference system's release notes cover to cover and extracted
every feature/capability into a categorized inventory (~300 entries after light deduplication). A
follow-up verification pass then checked each of the resulting candidate gaps against this codebase's
actual current state (not assumptions) before it was allowed onto this list — see the "Current state"
column below, each backed by a concrete file/ADR citation.

## Capability gaps (candidates for Phase 31)

| # | Capability | Reference system has it since | Current OG Doc state | Gap |
|---|---|---|---|---|
| 1 | **Draft / pre-registration objects** — create a file/case/document informally, before it receives an official reference number, and only "register" it (assigning the number) once it's ready | Launch release (2019) | **Missing entirely.** `Kennzeichen` is always assigned synchronously and irreversibly at `POST /documents`/case creation whenever the object type has a generator configured (P5e-S2) — there is no unregistered/informal working state. | New two-phase object lifecycle: create without a number → explicit "register" action assigns it. |
| 2 | **Multi-level classification (VS-Grade) with a dedicated gating role** — a document's confidentiality level is a real per-document attribute, and only a "confidentiality/security-clearance officer" role can set it | Launch release (2019) | **Partially exists.** `ClassificationLevel` (`VS-NfD`/`VS-VERTRAULICH`/`GEHEIM`/`STRENG GEHEIM`) is modeled, but only as a fixed default on the **object type**, never settable per document (`object_type_service/models.py:79`). All four levels trigger the exact same binary deletion gate (ADR 0059) — no role differentiation beyond the general `admin.object_config` capability. | Make classification a real per-document field; add a dedicated role required to set/raise it. |
| 3 | **Document redaction with burn-in** — produce a redacted copy with content permanently removed, linked back to the original, safe to disclose | Launch release (2019), web-client support added 8.4.1500 | **Missing the actual mechanism.** `document-service` has generic "derived working copy" provenance fields (`derived_from_document_id` etc., P6-S3) that its own docs cite redaction as an *example* use case for — but no code anywhere actually removes/masks content, and `case-service` never calls it. | Build an actual redaction workflow: apply redaction regions → burn into a new PDF rendition → link original↔redacted via a typed reference, with the redacted copy excluded from full-text indexing of the original content. |
| 4 | **Deletion reason codes** — a controlled, admin-extensible vocabulary of justifications recorded whenever a record is permanently deleted | 1.0.18xx (2019) | **Missing.** Force-delete already requires four-eyes approval (P6-S4/ADR 0060 default), but captures no "why." | Add a `deletion_reason` field (enum + "other: free text" fallback) to the existing force-delete flow; no new approval mechanism needed. |
| 5 | **Records quarantine / retention-hold** — move a record into an administered holding area with a configurable auto-delete condition, as an access-control and destruction-scheduling mechanism distinct from Legal Hold | 9.0.1100 (2023) | **Does not exist for records.** The only "quarantine" concept in this codebase is virus-scan holding (ADR 0052) — an unrelated, narrower mechanism (pending/failed scan results, not a records-governance tool). | New quarantine mechanism on documents/cases: restricted visibility, configurable retention/deletion condition, explicit release action — reuses the Legal Hold RBAC pattern (ADR 0075) rather than inventing new roles. |
| 6 | **Central + decentralized inbox model with a cross-inbox routing registry ("Postbuch")** — one central mail intake plus per-department inboxes, with a searchable log of every item's routing history between them | Launch release (2019), heavily extended through 10.1 | **Does not exist.** `mail-connector` models exactly one mailbox per instance, routed via regex/reference-number match directly to a folder/case (`docs/services/mail-connector.md`) — no multi-inbox concept, no routing-history registry. | A larger extension of `mail-connector`: multiple named inboxes (central + department), a `mail_routing_log` table, and a searchable register view. Sized as its own multi-session effort, not a single session. |
| 7 | **Dynamic organizational-hierarchy-based temporary access grants** — a workflow task can grant the assignee's supervisor, the full supervisor chain, or the assignee's/creator's org unit temporary access to the case, resolved at runtime from the org chart | 9.0.1200 (2023) | **Does not exist.** `permission-service` has delegation (ADR 0048, self-service, explicit grantor→deputy) and scope-locks, but nothing resolves access dynamically from an org-chart position at task-assignment time. | Needs an org-hierarchy concept in `auth-service`/`permission-service` first (none exists today — group membership is flat) before the dynamic-grant mechanism itself is buildable. Flagged as higher-risk/larger scope. |
| 8 | **Output stamping** — configurable text/barcode/QR stamped onto documents at export, print, e-mail dispatch, or handoff to an external system, for paper-trail reconciliation | 9.0.1200 (2023), extended 9.1.500 | **Partially exists.** `rendering-service`'s `watermark.py` is a generic, on-demand, text-only diagonal stamp via a standalone `POST /render/watermark` endpoint — no barcode/QR, no position control, and not automatically wired into export/print/e-mail/handoff flows (must be called deliberately each time). | Extend `watermark.py` with barcode/QR support and configurable position; wire it as an optional automatic step into the Phase 28 export pipeline and (once built) any Fachverfahren-style handoff. |
| 9 | **Hand folders / work trays** — a hand folder assembles references (not copies) to records from different cases into one working compilation; a work tray is an informal, permission-securable pre-record collaboration area later promotable to a real record | Launch release (2019) | **Does not exist as distinct object types.** `case-service`'s circulation folders and `folder-service`'s personal areas are the closest analogues but don't model either concept. | Two small new object-type-like concepts, likely modeled as configuration (folder/document object types in `object-type-service`) rather than new services — check for overlap with gap #1 (draft objects) before scoping, since a "work tray" is essentially a draft-object container. |
| 10 | **Supervisor/team task oversight view** — a manager sees every open workflow task across their direct reports | 8.4.1101 (2022) | **Does not exist.** `reviewer-ui`'s `TaskList` is a flat, instance-agnostic list of tasks the *current* user can act on (ADR 0041) — no cross-user, org-hierarchy-aware view. | Depends on gap #7's org-hierarchy concept for "who reports to me" — sequence after it, or ship a manually-configured team-membership version first if org-hierarchy is deferred. |
| 11 | **Accessibility (Barrierefrei) compliance pass** — accessible iconography/contrast for classification-related icons, gender-neutral system messaging, explicit warnings before producing non-accessible PDF exports | Ongoing across many releases | **Not addressed at all.** No accessibility-specific pass exists anywhere in this project's history. | A cross-cutting audit-and-fix session across all six frontend apps' icon/contrast/copy, plus a warning in the Phase 28 export flow when the source isn't tagged-PDF/accessible. Directly relevant to BITV/EU accessibility-directive obligations for public-sector software. |
| 12 | **xdomea/XJustiz as a general, bidirectional exchange format** — export *and* import structured file/case packages to/from other authorities' and courts' systems, for any case, not only during disposal | Launch release (2019); XJustiz specifically since 8.1.1100 (2021) | **Partially exists, narrowly.** ADR 0029's `xdomea.py` is export-only, XDOMEA 4.0.0-only, and scoped exclusively to `archival-service`'s disposal ("Aussonderung") pipeline for `Case` objects — no import direction, no XJustiz support at all (zero references in the repo). | A genuinely large effort: add XDOMEA import, extend beyond the disposal pipeline to general document/case export for inter-agency handoff, and add XJustiz for judiciary exchange. Likely its own multi-session sub-effort, sequenced after the higher-value items above. |

## Explicitly not prioritized

Consistent with this project's established triage discipline (see the Phase 18–26 "Nicht in dieser
Roadmap" precedent), the following reference-system capabilities are deliberately **not** proposed for
Phase 31, with reasoning:

- **Cross-tenant/cross-authority workflow participation via xdomea** (one installation's process handing
  a task to another installation's system) — depends entirely on gap #12 (general xdomea) being built
  first; premature to design before that foundation exists.
- **Third-party municipal long-term archive integration (a specific external archival system)** —
  vendor-specific integration target with no equivalent need identified for this project yet; revisit if
  a concrete installation requests it.
- **Java 11 runtime support, SBOM (CycloneDX) delivery, multi-solution coexistence in one instance,
  method-history API logging for analytics** — infrastructure/packaging concerns of the reference
  system's own deployment model, not gaps in this project's actual feature set. (This project already
  produces reproducible builds and has its own audit-service hash chain; a formal SBOM could be a small,
  separate ops task if ever needed, unrelated to the DMS feature set itself.)
- **Countless UI-parity/convenience fixes** cited throughout the reference system's history (bulk direct
  edit via context menu, reorderable task blocks, saved-search facets, etc.) — real but minor, better
  addressed opportunistically during related future work than as their own sessions.

## Confirmed already-covered ground

For completeness, the following reference-system capability areas already have a working equivalent in
this project and are excluded from the gap list above:

- Aktenplan-style object-type hierarchy, reference-number generation with attribute placeholders,
  business calendars, mailroom role, four-eyes defaults for sensitive actions — `packages/egov/`
  (ADR 0059/0060).
- Circulation-folder-style approval/acknowledgement/task workflow templates — `packages/egov/workflows/`
  (ADR 0060), built on the general workflow engine (Phase 6).
- Legal Hold, retention periods, forced deletion, deletion register, trash — Phase 7 family, ADR 0075.
- Records disposal / long-term archival handover — `archival-service` (Phase 7, Phase 11).
- Delegation (deputy access, time-limited, auditable "on behalf of" tagging) — ADR 0048. Partially
  extensible (two of three scope dimensions are inert, only `workflow-service` enforces it) but the core
  mechanism exists and doesn't need to be rebuilt.
- Digital signatures — `signature-service` (ADR 0025).
- PDF export with export history, combined folder export — Phase 28.
- Authenticated direct links, configurable email templates — Phase 29/30.
