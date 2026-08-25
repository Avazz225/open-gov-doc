# 0118 — Hand folders and work trays: a reference join in folder-service, no new object type behavior

**Status:** accepted (P31-S7, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 7 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #9 "Hand folders / work
trays"), affects `folder-service`, `document-service`, `packages/egov/config.json`

## Decision

**Hand folder (Handakte)** — "assembles references (not copies) to records from different cases into
one working compilation": a new `FolderDocumentReference` table in `folder-service` (`folder_id` real FK
to `folder.folder.id`, `document_id` opaque, `added_by`/`added_at`, soft-removed via
`removed_by`/`removed_at`), structurally mirroring case-service's existing `CaseDocumentReference` minus
the case-closure snapshot mechanism. Three new endpoints (`POST`/`DELETE`/`GET
/folders/{folder_id}/document-references`), gated by a new, folder-scoped `folder.read`/`folder.write`
permission-service check — the first real consumer of folder-service's own resource tree from *within*
folder-service itself. Any folder can hold references (not gated by object type); a new "Handakte" object
type (name + icon only) exists purely for discoverability.

**Work tray (Arbeitsvorrat)** — "an informal, permission-securable pre-record collaboration area, later
promotable to a real record": deliberately **not** a new entity. It is the composition of three things
that already exist or are added elsewhere in this session: (1) a **teamspace** (self-service, permission-
securable, real folder-backed collaboration area, unchanged) as the "informal... area", (2) `document-
service`'s existing draft/pre-registration lifecycle (ADR 0113) as the "pre-record" state, and (3) a new
`POST /documents/{document_id}/promote` endpoint — register (assign the reference number) plus an
optional move to a real destination folder, as one atomic action/event — as "promotable to a real
record". A new `GET /documents?folder_id=...&registered=true|false` filter makes a work tray's contents
actually browsable as a tray (list only the still-unregistered items). A new "Arbeitsvorrat" object type
(name + icon only) exists purely for discoverability, usable on any folder including a teamspace's root.

## Rationale

- **Why a hand folder needed genuinely new backend code, not just object-type config**: verified against
  every candidate reuse before building anything. `case-service`'s `CaseDocumentReference` is the only
  existing "reference not copy" join in the codebase, but it is hard-bound to exactly one `case_id` via a
  real FK, and a `Case` mandatorily carries a `process_definition_id` and starts a real BPMN process
  instance at creation (`case-service/main.py`) — there is no "case without a process" to lean on, and no
  container that spans cases. `object-type-service` has no behavior-hook mechanism of any kind on
  `ObjectType` — every object-type-driven behavior in this codebase is bespoke code in a consuming service
  reading one purpose-added column (documented explicitly in `docs/services/object-type-service.md`: "this
  service does not enforce ... — it only delivers it on request"). So a "Handakte" object type alone would
  have given a name, an icon, and placement rules for free, and exactly zero behavior.
- **Why folder-service, not a new small service or a document-service table**: `folder-service` is the
  **only** resource type that actually feeds permission-service's `ResourceNode` tree (documents and cases
  are not permission resources at all, per `docs/services/permission-service.md`/`docs/services/case-
  service.md`). A hand folder is explicitly meant to be a *working compilation*, potentially surfacing
  documents from several different, possibly sensitive cases in one place — of every place in this system
  a genuinely per-object-securable "compilation" container could live, a real folder is the only one that
  gets real, working RBAC without inventing a new permission-resource concept from scratch. Building the
  reference table as a new `folder_document_reference` row (mirroring `CaseDocumentReference`'s exact
  container-owns-the-join-table precedent, just with `Folder` instead of `Case` as the container) reuses
  the maximum amount of existing infrastructure: `folder-service` already has a working `DocumentClient`
  (`cascade_trash`/`cascade_restore`/`count_active`) that only needed one more method (`.get()`, an exact
  mirror of case-service's own), and already integrates with `permission-service` (`admin.legal_hold`).
- **A real, folder-scoped RBAC check, not the mostly-open status quo**: `folder-service` today enforces
  essentially no permission of its own (only `admin.legal_hold`, see "Open Points" in
  `docs/services/folder-service.md`) — the resource tree exists and is populated correctly, but almost
  nothing inside folder-service actually queries it (per research, only `teamspace-service` and a handful
  of `document-service`/`search-service` call sites anchor real checks against it today). A hand folder is
  exactly the kind of feature where this matters: it deliberately concentrates cross-case references in
  one place, which is a materially different risk profile from ordinary folder browsing. `POST`/`DELETE`
  require `folder.write` on the target folder, `GET` requires `folder.read` — both checked via
  `PermissionServiceClient.check(resource_id=folder_id, ...)`, the exact same generic, resource-scoped
  primitive `teamspace-service`/`document-service`'s export/redaction endpoints already use elsewhere.
  Existence is resolved before permission (`404` before `403`) — the same ordering the public share-link
  creation endpoint already established (ADR 0047): a resource-scoped permission check is only meaningful
  once the resource is confirmed to exist.
- **No uniqueness constraint on the reference join, no case-status-style lifecycle gate**: mirrors
  `CaseDocumentReference`'s own permissive precedent exactly (the same document may be referenced twice;
  harmless) and correctly omits `Case`'s `status != "open"` guard, since a folder — unlike a circulation
  folder — has no terminating BPMN-driven lifecycle to guard against.
- **Work tray: explicitly NOT a new entity**, per the plan's own instruction to check for overlap with
  gap #1 (draft objects, ADR 0113) before modeling it separately. Two separate research passes confirmed
  why neither existing piece is sufficient *alone*, and why composing them is correct:
  - **Draft/pre-registration lifecycle alone is not "pre-record"**: a draft document (or case) is a fully
    real row from the first millisecond — ADR 0113's own "Consequences" section says so explicitly. It
    defers only the reference-number assignment, not existence, folder placement, or (for a case)
    starting a real workflow instance. There was also, before this session, no way to *list* drafts as a
    distinct group (`registered_at IS NULL` existed as a marker but no query surfaced it) and no
    "promotion" action beyond number assignment — exactly the two gaps this session closes for documents.
  - **Teamspaces alone have no notion of pre-record status.** They already provide everything the
    "informal, permission-securable... area" half of the definition needs (self-service creation, a named
    member set, a real backing folder, resource-scoped permission-service anchoring on that folder) — see
    `docs/services/teamspace-service.md`. Building a second, parallel "work tray" collaboration mechanism
    next to it would be pure redundancy the plan explicitly warned against.
  - Composing them costs almost nothing new: two thin additions (a query filter, an action endpoint) turn
    "upload drafts into any folder, including a teamspace's" into a genuinely useful work-tray workflow,
    without a new entity, a new table, or a new permission model.
- **Documents only, not cases, for the promotion mechanism**: a case cannot be an "informal, no-process
  pre-record object" even in draft form — `POST /cases` mandatorily starts a real BPMN process instance
  regardless of the `draft` flag (only the `vorgangsnummer` assignment is deferred). A case's "draft" is
  therefore a different kind of thing from a document's — it already has a full process attached. Scoping
  `promote`/the `registered` filter to `document-service` only mirrors the exact scoping conclusion this
  project already reached for redaction (ADR 0115) and quarantine (ADR 0116) after `case-service` turned
  out to have no realistic hook for each of those features either.
- **`promote` is one atomic action/event, not two frontend-orchestrated calls**: `register` (assign the
  Kennzeichen) then `PATCH .../folder_id` (move) are both already independently callable — a frontend
  *could* chain them. But that risks a genuinely inconsistent intermediate state (register succeeds, the
  move then fails against an already-registered document) and produces two generic events instead of one
  dedicated, auditable `document.promoted` entry for what the concept explicitly names as a first-class
  action. `promote_document` therefore validates the target folder *before* calling `register_document`
  (so a `400` for an unknown target folder leaves the document completely untouched, still a draft) and
  commits both steps together. Deliberately ungated, exactly like the two existing primitives it replaces
  (`register`, and the PATCH move branch) — adding a new permission check here that neither underlying
  operation has today would be an arbitrary inconsistency this session's scope doesn't call for.

## Consequences

- **Real bug found and fixed during this session's own live verification, not by a test**: deleting a
  folder with an active hand-folder reference (via either the plain `DELETE /folders/{id}` endpoint or
  `hard_delete_folder`, used by forced deletion/purge) raised a Postgres `ForeignKeyViolationError` —
  `folder_document_reference`'s FK on `folder.folder.id` was never cleaned up before the folder row
  itself. Fixed by mirroring the exact `legal_hold`-cleanup pattern `hard_delete_folder` already used, via
  a new existence-check-free `_list_document_references_raw()` helper (the *public* `list_document_
  references()` deliberately 404s on an unknown/already-trashed folder — reusing it inside the deletion
  cascade broke `hard_delete_folder`'s existing "works on an already-soft-deleted folder" contract, caught
  immediately by two now-failing pre-existing tests when first attempted). Two new regression tests added
  (`test_repository.py`/`test_retention_actions.py`) reproduce the exact live failure.
- A document can be moved to a target folder it has no automatic hierarchy validation exemption for —
  `promote` reuses the exact same `object_type_client.validate(parent_object_type_id=..., parent_is_root=
  ...)` check the PATCH move branch already performs, so promoting into a folder that violates the target
  object type's `allowed_parent_types` still correctly fails with `400`.
- **No cross-case index or search surfacing for hand folders**: a hand folder's compilation is only
  visible by opening that specific folder's reference list — there is no global "which hand folders
  reference document X" reverse lookup, and `search-service` is not made aware of these references. A
  reasonable follow-up, not required for the mechanism itself to work (same category of deliberate,
  documented scope limit as ADR 0116's search-service omission for records quarantine).
- **`folder.read`/`folder.write` are new, not-yet-open permission strings** (confirmed absent from
  permission-service's `EVERYONE_ROLE_PERMISSIONS` default set) — an installation must explicitly grant
  them per hand folder (or via a group) before anyone besides the creator can curate/view one. This is the
  intended, deliberate behavior, not a gap to close later.
- **Work trays inherit teamspace-service's own documented RBAC limitation**: permission-service anchoring
  on a teamspace's root folder is not the *primary* enforcement mechanism anywhere except `search-service`
  today (`docs/services/teamspace-service.md` "Open Points") — `folder-service`'s/`document-service`'s
  own direct CRUD on a teamspace folder's contents is not actually protected by teamspace membership.
  Closing that gap is a materially larger, separate RBAC-hardening effort (in the shape of Phase 19's own
  sessions) and out of this session's scope; a work tray built on a teamspace is exactly as securable in
  practice as a teamspace already is, no better and no worse.
- **No new admin-ui/user-ui surface for browsing "all hand folders"/"all work trays" installation-wide**
  — both are reachable only by navigating to the specific folder in question (the explorer, or a
  teamspace's own pane), same as how any other folder-based feature in this project works; a dedicated
  cross-folder overview is a reasonable, separate future cut.
