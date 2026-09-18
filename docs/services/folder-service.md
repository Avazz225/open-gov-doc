# folder-service

**Responsibility:** Folders as hierarchical containers (concept 2.1) — create, rename, move, delete (only when empty, or cascaded via the trash), optional object-type validation. Owns the folder hierarchy and publishes structure events, through which the Permission Service keeps its permission inheritance in sync. Since P7-S1b, additionally retention/legal hold/forced deletion including the deletion register (5.2/5.2a), a 1:1 match of the same pattern as `document-service` (P7-S1) — cascading in the process to contained documents.

**Concept Reference:** 2.1, 5.2/5.2a (since P7-S1b), 14.2 (hand folders, Post-Roadmap Phase 31 Session 7)
**Own Postgres Schema:** `folder` (tables `folder`, `legal_hold`, `deletion_register_entry`, `retention_config`, `trash_config`, `folder_document_reference`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/folders` | Create (`name`, `parent_id` default `"root"`, optional `object_type_id`/`attributes`, `created_by`) |
| `GET` | `/folders/deleted?parent_id=` | Trash contents of a folder (5.2, since P7-S1b). Alternatively `?scope=personal\|admin` instead of `parent_id` (installation-wide trash, 2.5, since P15-S1), see "Trash Family" below |
| `GET` | `/folders/{id}` | Metadata — treats a soft-deleted folder as nonexistent (404) |
| `GET` | `/folders/{id}/children` | Direct subfolders (non-deleted) |
| `PATCH` | `/folders/{id}` | Rename and/or move (`parent_id`) and/or change attributes — `inbox`/`outbox` (2.5, since P15-S3) reject a set `name`/`parent_id` with `409`, pure attribute changes remain allowed |
| `DELETE` | `/folders/{id}` | Immediate hard delete — 409 if subfolders still exist, or if `inbox`/`outbox` (since P15-S3). Fallback for cases that are already empty and never subject to retention; the regular path since P7-S1b is `POST .../trash` |
| `POST` | `/folders/{id}/purge` | Manual, immediate permanent deletion of a folder already in the trash (2.5, since P15-S1) — `409` if not in trash or subtree not empty, `403` without the `admin.deletion` capability (ADR 0150), see "Trash Family" below |
| `POST` | `/folders/{id}/trash` | Trash path (5.2, since P7-S1b) — cascades across the entire active subtree. Since **P7-S1c** optionally gated by the four-eyes principle (action type `folder.delete`, deletion-request workflow for regular users) — response `TrashResult{status: "trashed"\|"pending_approval", folder, approval_request_id}`. `409` for `inbox`/`outbox` (since P15-S3) |
| `POST` | `/folders/{id}/restore` | Trash restoration including cascaded subfolders/documents (5.2, since P7-S1b) |
| `PUT` | `/folders/{id}/retention` | Schedule retention period/forced deletion (5.2/5.2a, since P7-S1b) |
| `POST` | `/legal-holds` | Set legal hold (5.2, since P7-S1b) — since **Post-Roadmap Phase 19 Session 10** ([ADR 0075](../adr/0075-legal-hold-rbac.md)) gated by `admin.legal_hold` |
| `POST` | `/legal-holds/{id}/release` | Release legal hold — since **P19-S10** likewise gated by `admin.legal_hold` |
| `GET` | `/legal-holds?folder_id=&active_only=` | Legal holds of a folder |
| `POST` | `/folders/{id}/document-references` | Add a hand-folder reference (`document_id`, `added_by`, 14.2, Post-Roadmap Phase 31 Session 7, ADR 0118) — `401`/`404`/`403`/`400` (unknown document), gated by `folder.write` on `{id}`, see "Hand Folders" below |
| `DELETE` | `/folders/{id}/document-references/{document_id}` | Remove a reference (soft, `removed_by`) — same gating |
| `GET` | `/folders/{id}/document-references` | List a folder's references, including removed ones (soft-removed, for traceability) — gated by `folder.read` on `{id}` |
| `GET` | `/deletion-register?folder_id=` | Deletion register (5.2a, since P7-S1b) |
| `POST` | `/folders/{id}/reconcile-restore-deletion` | Deletion reconciliation after restore (10.4, since P11-S4) — `X-DMS-Roles: dms-admin`, 1:1 the same pattern as `document-service` |
| `GET`/`PUT` | `/retention-config` | Installation-wide default retention settings for folders (standalone, not the same config as `document-service`), incl. its own `deletion_reason_catalog` since Post-Roadmap Phase 31 Session 1 ([ADR 0112](../adr/0112-deletion-reason-catalog-ux-not-enum.md)) |
| `GET`/`PUT` | `/trash-config` | Trash restoration period for folders (standalone) |
| `POST` | `/folder-templates` | Capture a structure template from a subtree (2.5/7.3, since **P15-S6**) — `404` for unknown `source_folder_id`, see "Structure Templates" below |
| `GET` | `/folder-templates` | All templates (without structure, metadata only) |
| `GET` | `/folder-templates/{id}` | Single template including the full structure tree — `404` for unknown `id` |
| `DELETE` | `/folder-templates/{id}` | Delete template (only the definition, not any already-applied folders) |
| `POST` | `/folder-templates/{id}/apply` | Apply template below `target_parent_id` — creates real folders, `404` for unknown template/unknown target |
| `GET` | `/healthz` | Health check |

A root folder (`id: "root"`) is created idempotently at startup — analogous to the Permission Service's `ROOT_RESOURCE_ID`. Since P15-S3, additionally two fixed special folders **`inbox`**/**`outbox`** (mail inbox/outbox, 2.5/3.3) directly under `root`, using the same idempotency pattern (`repository.ensure_special_folders`). All three are protected against renaming/moving (`PATCH`, 409 for a set `name`/`parent_id`) and deletion (`DELETE`/`POST .../trash`, 409) — a special area "exists exactly once per installation" (2.5). `root` only received this protection retroactively in Post-Roadmap Phase 19 Session 11 ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)).

## Data Model

`folder`: `id`, `name`, `parent_id` (self-FK, nullable only for `root`), `object_type_id` (opaque reference to the Object-Type Service, integer), `attributes` (JSON), `created_by/at`, `updated_at`. Since P7-S1b, additionally: `deleted_at`, `deleted_via_folder_id` (cascade origin, see below), `retention_until`, `full_deletion`, `pending_deletion_reason`, `deletion_reminder_sent_at`, `reminder_notify_email`, `force_delete_approval_requested_at` — structurally identical to `document_service.Document`'s corresponding fields (P7-S1). Since P15-S1, additionally `deleted_by` (prerequisite for the personal trash, 2.5).

`legal_hold`/`deletion_register_entry`/`retention_config`/`trash_config`: structurally identical to the `document-service` counterparts (see there), but standalone tables with `folder_id` instead of `document_id` — **no** reuse of `document-service` tables across service boundaries (no cross-schema FK, no premature centralization into a compliance service, same rationale as in P7-S1). This allows an installation operator to configure different requirements (restoration period, deletion-reason requirement) for folders than for documents.

`folder_document_reference` (14.2, Post-Roadmap Phase 31 Session 7, [ADR 0118](../adr/0118-hand-folders-and-work-trays.md)): `id`, `folder_id` (real FK to `folder.folder.id`), `document_id` (opaque, no FK — the document lives in `document-service`), `added_by`/`added_at`, `removed_by`/`removed_at` (nullable, soft-removed for traceability) — structurally mirrors `case-service`'s `CaseDocumentReference` minus its case-closure snapshot mechanism, see "Hand Folders" below.

## Object-Type Validation (2.2/4.5)

If a folder carries an `object_type_id`, `POST /object-types/{id}/validate` of the Object-Type Service is called on creation (name + attributes) — if validation fails, the folder is not created (400 with an error list). Without `object_type_id`, the check is skipped entirely. Since P7-S1b, additionally: if the object type carries a `default_retention_days`, a concrete `retention_until` date is applied once on creation — identical pattern to `document-service` (the field itself was already introduced across object types in P7-S1).

**Enforced object hierarchy (2.2a, since P5b-S1, ADR 0013)**: the same validation call additionally transmits placement information about the intended parent folder — `parent_is_root: true` if `parent_id == "root"`, otherwise `parent_object_type_id` (the parent folder's `object_type_id`, already known locally from the service's own `folder` table, `None` if untyped). The Object-Type Service resolves the parent class name from this itself and checks it against any `allowedParentTypes` of the type being placed. Checked both **on creation** and **on move** (`PATCH /folders/{id}` with a changed `parent_id`).

**Bug fix (P14-S12)**: `PATCH /folders/{id}` previously only validated on an actual move (`is_move`) — a pure attribute/name change without a move bypassed object-type validation entirely, unlike `document-service`'s `update_document` (there, validation runs on **every** PATCH request once an object type is set). Found during research into bulk metadata editing (concept §8, ADR 0050) — without the fix, a bulk attribute change on folders would have effectively not enforced the required constraint check for folders. Now symmetric with `document-service`: validates on every change, not only on move; `_validate_against_object_type()`'s placement parameters are only populated on an actual move, otherwise left empty.

**Bug fix — move-cycle detection (Post-Roadmap Phase 38 Session 1)**: `repository.update_folder` previously only rejected a folder being moved directly under itself (`new_parent_id == folder_id`) — a deeper cycle (e.g. `A` moved under its own child `B`, having previously moved `B` under `A`) went through unchecked, permanently making every folder in the cycle undeletable (`delete_folder`'s not-empty check sees the other as a child either way) and unreachable from any root-anchored tree walk. Already found and documented live at P23-S4 (drag-and-drop's own client-side guard only protects the currently-loaded part of the tree, not direct API calls), but deliberately left unfixed there as out of that session's frontend-only scope. Now closed: before applying a move, `update_folder` checks whether the intended new parent appears in the folder's own active subtree (`list_active_subtree_ids`, the same helper the trash cascade already uses) and rejects with a `ValueError`/`400` if so — since a folder is always the first element of its own subtree, this one check also covers the original direct-self-parent case, replacing the narrower `new_parent_id == folder_id` guard entirely.

## Hand Folders (14.2, Post-Roadmap Phase 31 Session 7, [ADR 0118](../adr/0118-hand-folders-and-work-trays.md))

"Assembles references (not copies) to records from different cases into one working compilation" — a
folder's reference to a document living elsewhere in the hierarchy, structurally mirroring `case-
service`'s existing `CaseDocumentReference` (a container's reference-not-copy join, container-owns-the-
join-table) minus the case-closure snapshot mechanism, since a folder has no terminating BPMN-driven
lifecycle the way a circulation folder does.

- **Any folder can hold references** — deliberately not restricted to a particular object type. A new
  "Handakte" object type (`packages/egov/config.json`, name + icon only) exists purely for naming/
  discoverability, not as an enforced precondition — same as how any folder can already hold a legal hold
  or a retention setting.
- **`POST`/`DELETE .../document-references` require `folder.document_reference.write`, `GET` requires
  `folder.document_reference.read`** — both checked, folder-scoped, against `resource_id={id}` via
  `PermissionServiceClient.check()`, the same generic primitive `teamspace-service`/`document-service`'s
  export/redaction endpoints already use elsewhere. Originally `folder.write`/`folder.read` (this was the
  first actual consumer of folder-service's own resource tree from within folder-service itself, at a time
  when almost nothing else in this service checked any permission at all) — renamed to a dedicated pair in
  **Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)),
  once core folder CRUD started checking the plain `folder.read`/`folder.write` strings too and granting
  them to "everyone" by default — reusing the same strings here would have silently handed every
  authenticated principal hand-folder curation rights. Existence is resolved before permission (`404`
  before `403`), the same ordering the public share-link creation endpoint already established
  (document-service, ADR 0047).
- **Live resolution, same two-field shape as case-service's `_resolve_reference`**: `GET .../document-
  references` calls `DocumentClient.get(document_id)` (a new method, exact mirror of case-service's own)
  for every still-active reference, returning `current_version_number`/`document_deleted_at` alongside
  the reference row — a soft-deleted original stays resolvable (`GET /documents/{id}` never 404s on a
  soft delete), so "a reference survives the deletion of its original, traceably" needs no extra logic
  here.
- **No uniqueness constraint, no case-status-style lifecycle gate** — same permissive precedent as
  `CaseDocumentReference` (the same document may be referenced twice, harmless); removed references are
  soft-removed (`removed_by`/`removed_at`), not deleted, and remain in `GET .../document-references`'s
  result for traceability.
- **Deliberately not gated on the referenced document's own permissions** — curating a hand folder's
  compilation requires write access to the *hand folder*, not to each referenced document individually;
  a reference doesn't grant any new access to the document itself, it only records that this compilation
  points to it. Opening/reading the actual document content is still subject to whatever access control
  already applies to it elsewhere — since **Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)),
  that means a real `document.read` check on document-service's own primary paths, not the previously
  nonexistent check this bullet used to describe.
- **`delete_folder`/`hard_delete_folder` both remove a folder's own `folder_document_reference` rows
  first** — found live during this session's own verification (an active reference otherwise violates the
  table's FK on `folder.folder.id` with a `500`), via a new existence-check-free `_list_document_
  references_raw()` (the public `list_document_references()` correctly 404s on an unknown/already-trashed
  folder, which broke `hard_delete_folder`'s "also works on an already-soft-deleted folder" contract when
  first tried with the public function).
- **`folder.document_reference.added`'s event payload carries `added_at`** (since **Post-Roadmap Phase 35
  Session 4**, [ADR 0146](../adr/0146-hand-folder-cross-index-and-work-tray-browsing.md)) — added for
  `search-service`'s new cross-folder index, the first consumer of this event. This endpoint's own
  reference-listing `GET` (the natural place a consumer would otherwise reload the real timestamp from) is
  itself `folder.read`-gated, unlike every other single-resource `GET` this service exposes, so the event
  carries the field directly instead.

## Retention, Legal Hold & Forced Deletion (5.2/5.2a, since P7-S1b)

Carries over the pattern built in P7-S1 for documents (see `docs/services/document-service.md` for the detailed rationale of the poll loop/legal hold/four-eyes principle) to folders — folders previously had **no** soft-delete concept whatsoever. Two points differ substantially from the document variant:

- **Cascading trash**: `POST /folders/{id}/trash` moves not only the folder itself into the trash, but recursively the entire **active** subtree (`repository.list_active_subtree_ids`) — subfolders are marked directly as well (`deleted_via_folder_id` = ID of the folder actually clicked, not of each direct parent folder), contained documents via a **synchronous** REST call to `document-service` (`document_client.py`, `POST /documents/cascade-trash`) — synchronous instead of event-based, so that e.g. an immediate `GET /documents/deleted` after deletion is already consistent. Subfolders/documents already deleted independently remain untouched (their cascade origin is not overwritten). `POST /folders/{id}/restore` mirrors this exactly: cascades back via a `deleted_via_folder_id` filter, calls `document_client.cascade_restore` — an independently, individually deleted document in the same folder remains in the trash.
- **No automatic cascading forced deletion**: before the `_retention_poll_loop` physically removes a folder with `full_deletion=true`, it checks via `document_client.count_active()` and its own subtree query whether active subfolders/documents still exist — if so, forced deletion is skipped for this tick (logged, next attempt on the next tick), with **no** automatic forced deletion of the contents cascaded along. A deliberate, conservative design decision: automatically extending physical deletions to an entire subtree would be a substantially greater risk than the deliberately accepted "stays stuck until manually emptied" (see "Open Points"). Since **Phase 44 Session 3** ([ADR 0164](../adr/0164-maintenance-mode-poll-loop-coverage.md)), the whole `_retention_poll_loop` tick is skipped while system-wide maintenance mode (4.8) is active.
- **Four-eyes principle**: new action type `folder.force_delete`, an exact copy-paste pattern of `document.force_delete` (own `approval_client.py`/`consumer.py` in this service) — no change to `permission-service` needed.
- **Deletion reminder**: `folder.deletion.reminder` event, consumed by a new `notification-service` consumer (1:1 copy of the `document.deletion.reminder` consumer, only `name` instead of `title` in the payload).
- Storage relevance: none — folders have no content of their own, `hard_delete_folder` is a pure DB row removal (after cleaning up the legal-hold history, same intermediate-flush pattern as `document_service.repository.hard_delete_document`).
- **Authorization (Post-Roadmap Phase 38 Session 3)**: `PUT /folders/{id}/retention`, `PUT /retention-config`, and `PUT /trash-config` previously had no permission check at all. All three now require `X-DMS-Principal` + the capability `admin.retention` (role `domain-admin-retention`), shared with the identical three endpoints on `document-service` — the same retention/disposal-policy concern for both resource types. See `docs/services/document-service.md` "Retention & Legal Hold" and [ADR 0148](../adr/0148-admin-ui-authorization-full-alignment.md).

## Deletion-Request Workflow for Regular Users (5.2, since P7-S1c)

Its own action type `folder.delete`, separate from `folder.force_delete` — the latter remains for retention-triggered forced deletion, `folder.delete` gates the manual, user-triggered `POST /folders/{id}/trash` (gate check directly in the endpoint, `TrashResult` wrapper). On approval, a new `consumer.py` branch (`_handle_delete_approved`) executes `repository.soft_delete_folder` — identical cascade to subfolders/documents as with a direct call. No new self-approval logic needed (`permission-service` already generically prevents initiator == approver). See `docs/services/document-service.md` for the detailed architecture rationale (identical pattern) and `docs/services/user-ui.md` for the new approval inbox.

## Trash Family: Personal Trash (2.5, since P15-S1)

Folder counterpart to `document-service`'s section of the same name — see there for the full rationale and [ADR 0051](../adr/0051-papierkorb-familie-classification-via-object-type-scoped-global-endpoints.md). No classified-documents variant: concept 2.5 explicitly marks only documents as classified documents, not folders.

- **`deleted_by` retrofitted**: `repository.soft_delete_folder` already accepted `deleted_by` as a parameter, but never persisted it (same gap found at P15-S0 as in `document-service`) — now a real column, reset again on `restore_folder`.
- **`scope` query parameter on `GET /folders/deleted`** (`personal`/`admin`) — purely additive, unchanged folder-related behavior without `scope`. `scope=personal` filters by `deleted_by == X-DMS-Principal` (401 without a principal header), `scope=admin` requires the `admin.deletion` capability (`_require_deletion_permission`, `has_permission`, role `domain-admin-deletion`, 403 otherwise) — since **Post-Roadmap Phase 39 Session 1** ([ADR 0150](../adr/0150-domain-admin-deletion-capability-migration.md)), replacing the previous `trash_hard_delete_admin_role` string-role setting via `X-DMS-Roles`, which no longer exists.
- **`POST /folders/{id}/purge`**: manual, immediate permanent deletion of a folder already in the trash and already empty — calls the same `retention_actions.purge_expired_trash_entry()` as the poll loop (now extended with `trigger`/`triggered_by`, `trigger="manual_purge"`). Same safety check as automatic forced deletion: `has_any_child_folder_row`/`document_client.count_active` must be empty (409 otherwise), otherwise physical removal would fail on the FK constraint — a nested tree must therefore be emptied leaf-up, one at a time, no recursive bulk purge. Gated by the `admin.deletion` capability (`_require_deletion_permission`) since **Post-Roadmap Phase 39 Session 1** ([ADR 0150](../adr/0150-domain-admin-deletion-capability-migration.md)), replacing the previous `trash_hard_delete_admin_role` string-role check — exact structural counterpart to `document-service`'s own `POST /documents/{id}/purge` regular branch.
- **`get_folder_any_state`** (new, public repository function) — counterpart to `get_folder` WITHOUT the trash filter, for the purge endpoint, which needs to address an already-deleted folder.

## Structure Events (Contract with Permission Service)

Publishes (stream `folder`, `ensure_stream=True`) exactly the contract the Permission Service had provisionally expected since P2-S2 (`docs/services/permission-service.md`):

| event_type | payload |
|---|---|
| `folder.resource.created` | `{resource_id, parent_id, resource_type: "folder"}` |
| `folder.resource.moved` | `{resource_id, new_parent_id}` (only if `parent_id` actually changes) |
| `folder.resource.deleted` | `{resource_id}` — since **Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)), published from every real hard-delete path (the direct `DELETE /folders/{id}` fallback, manual purge, retention-poll-driven forced deletion/trash-expiry purge, restore-reconciliation) — previously only the first of these five |
| `folder.trashed` | `{deleted_by}` (5.2, since P7-S1b) |
| `folder.restored` | `{}` (5.2, since P7-S1b) |
| `folder.retention.updated` | `{retention_until, full_deletion}` (5.2/5.2a, since P7-S1b) |
| `folder.legal_hold.set` / `folder.legal_hold.released` | `{set_by, reason}` / `{released_by}` (5.2, since P7-S1b) |
| `folder.deletion.reminder` | `{name, retention_until, full_deletion, notify_email}` (5.2a, since P7-S1b, consumed by `notification-service`) |
| `folder.force_deleted` | `{reason, triggered_by}` (5.2a, since P7-S1b) |
| `folder.trash_purged` | `{trigger: "trash_expiry"}` (5.2a, since P7-S1b) |

**Consumes** (since P7-S1b, this service's first consumer ever): `permission.approval.approved` — relevant for `action_type == "folder.force_delete"` (executes a forced deletion previously deferred via the four-eyes principle); all other action types are ignored.

**Audit hookup (since P7-S2, a genuine retrofit)**: `audit-service` was missing `"folder.>"` in its consumed subject list ever since this stream was introduced in P7-S1b — a pre-existing bug discovered during the P7-S2 live smoke test, fixed retroactively including a backfill of the complete prior folder event history (see `docs/services/audit-service.md`).

**`inbox`/`outbox` synchronous registration (Post-Roadmap Phase 38 Session 4, [ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md))**: unlike every other folder, `inbox`/`outbox` are bootstrapped directly into the DB at startup (`ensure_special_folders`), not via `create_folder`, so they never went through the `folder.resource.created` event above and had no `ResourceNode` in `permission-service` at all. Now registered synchronously at startup via `POST /resources` (the same idempotent, create-if-missing primitive ADR 0144 added) — needed once core folder CRUD started actually checking permission-service's resource tree, since an unregistered `resource_id` denies outright rather than falling back to an ancestor's grant.

## Structure Templates (2.5/7.3, since P15-S6)

A folder subtree as a named, reusable template (e.g. a file-plan skeleton) — the last session of Phase 15. Full architecture rationale: [ADR 0056](../adr/0056-struktur-vorlagen-folder-service-json-tree-no-attribute-values.md).

- **Built directly into `folder-service`, not layered on top of `config-service`'s existing 7.3 export** — already corrected at P15-S0 (the concept text does not technically apply, see `PROGRESS.md`).
- **`FolderTemplate` table with a nested JSON tree** (`structure`: `{"name", "object_type_id", "children"}`) instead of standalone "dead" `Folder` rows — `repository.build_template_structure` walks the active subtree recursively via the already-existing `list_children` (automatically excludes soft-deleted subfolders). No FK to `Folder` — a template remains valid even if the source folder is later moved/renamed/deleted.
- **Deliberately structure ONLY, no attribute values** — a "skeleton" is only filled in after applying (required attributes are then checked entirely regularly at `PATCH /folders/{id}`).
- **Applying deliberately skips object-type validation** — `repository.apply_template`/`_apply_structure_node` call `repository.create_folder` directly (not the validating `POST /folders` endpoint code path), thus creating folders with empty `attributes={}` regardless of whether the assigned object type has required fields. Parent-child object-type nesting rules (2.2b) are likewise not checked here — a deliberately documented limitation, see ADR 0056.
- **`folder.resource.created` is published individually for every newly created folder** (main.py, after the commit) — identical event/payload to a regular `POST /folders`, so `permission-service`'s `ResourceNode` tree stays in sync. Verified live against the running stack (`GET /resources/{id}` on `permission-service` confirmed both applied nodes).
- **Ungated** (no role required) — identical pattern to the regular `POST /folders`, no concept requirement calls for a restriction here.
- **Frontend uses raw folder-ID text inputs** for source/target folders instead of a purpose-built tree picker — identical, already-established pattern to `QuarantinePane`/`PoststellePane` (P15-S2/S3).

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry at startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) — the basis for API gateway routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values the service runs unchanged, without discovery.

## Sensors (Concept 10.1)

None yet — to follow in Phase 11.

## Tests

**143 tests since Phase 44 Session 2** (previously 140, +3: `test_manual_purge_publishes_resource_
deleted`/`test_reconcile_restore_deletion_publishes_resource_deleted`/`test_execute_or_defer_forced_
deletion_publishes_resource_deleted` in `test_events.py`, proving the new `folder.resource.deleted`
publish at three of the four newly-fixed call sites — the fourth, the retention poll loop's own
trash-expiry purge, shares the identical two-line fix shape as the manual-purge endpoint and is covered
by that same code path being exercised there, not given a separate live-poll-tick test of its own).
Test count before this session had drifted stale in this doc (last recorded as 137); actual count
immediately before this session's changes was 140.

Older history: previously 135, 2 new since Post-Roadmap Phase 38 Session 1: `test_update_move_into_own_descendant_raises` (`test_repository.py`) and `test_move_into_own_descendant_returns_400` (`test_api.py`) — regression tests for the move-cycle fix above, reproducing the exact A→B→A case previously confirmed live); before that 135, previously 120, 15 new since Post-Roadmap Phase 31 Session 7 (ADR 0118): 7
`test_repository.py` (add/list, unknown folder, duplicate allowed, remove marks removed not deleted,
remove-unknown raises, remove-already-removed raises, plus a regression test confirming `delete_folder`
removes an active reference instead of hitting the FK violation found live) + 7 `test_api.py` (401, 403,
400 unknown document, 404 unknown folder, full lifecycle incl. a soft-removed entry staying listed, list
requires `folder.read`, `folder.read` alone does not grant write) for hand-folder document references
+ 1 `test_retention_actions.py` (the same FK-violation regression, but through `hard_delete_folder`),
using the same resource-scoped role-grant test pattern already established for `document-service`'s
`test_export.py`) — before that 120, previously 99, 16 new since **P15-S6**: `test_folder_templates.py` — structure capture including exclusion of soft-deleted subfolders, unknown source folder, capture/list/read/delete roundtrip, applying creates the full subtree below the target without altering the source tree, unknown target; `test_api.py` extended for the four new `/folder-templates` endpoints including `404` cases); before that 99, previously 93, 6 new since **P15-S3**: `inbox`/`outbox` exist and are located under `root`, renaming/moving/hard-deleting/trashing `inbox` each give `409`, pure attribute changes remain allowed; before that 93, previously 79, 14 new since **P15-S1**: `test_api.py` gained tests for `scope` visibility on `GET /folders/deleted` (401/403/personal/admin filtering), `POST /folders/{id}/purge` (401/403/404/409-not-in-trash/409-remaining-child-row/204-success including a deletion-register entry), `test_retention.py` gained tests for `deleted_by` persistence/reset/filtering) (`test_api.py`, `test_repository.py`, `test_object_type_validation.py`, `test_events.py`, `test_retention.py`, `test_retention_actions.py`, `test_consumer.py`) — the last three files new since P7-S1b (cascade logic against a fake `DocumentClient`, poll-loop branches called directly as in `document-service`, four-eyes consumer integration, including a regression test for a real bug found during the live smoke test — see `PROGRESS.md`: the not-empty check before a forced deletion incorrectly treated a folder with only one already soft-deleted subfolder as empty and crashed on the Postgres FK constraint; `has_any_child_folder_row` has since additionally checked without a `deleted_at` filter).

## Open Points

- **No automatic cascading of forced deletion onto the contained subtree** (5.2a, since P7-S1b, see above) — a folder with still-active subfolders/documents remains untouched when forced deletion is due, until the subtree is emptied by other means (regularly or via trash expiry). A deliberate, conservative limitation of this foundation, not a known gap but an explicit design decision.
- No endpoint for breadcrumb/full path — only direct children can be retrieved, sufficient for current needs.
- Scope locks (4.7, "entire folder area locked for regular users") are not part of this session — conceptually they belong more to the Permission Service and are planned for a later phase.
- **No retroactive check for `allowedParentTypes` when the config changes later** (see ADR 0013) — the same limitation as in the Object-Type Service applies here. **Note (Post-Roadmap Phase 38 Session 1)**: re-investigated alongside the fix below and confirmed to be a genuinely different, much smaller concern than the move-cycle bug that used to be listed right underneath it — `allowedParentTypes` validation (`object-type-service`'s `validate_against_object_type`) only ever compares one level (the immediate intended parent's type name), it never walks a chain, so it cannot loop indefinitely or produce an undeletable state the way an unchecked `parent_id` chain could. This item is about two object *types* possibly being configured to allow each other as a parent class (a structural oddity, not a bug), not an instance-level cycle — deliberately left open, not part of this session's fix.
- ~~**`PATCH /folders/{id}` (move) only checks "not its own direct parent folder"** (`repository.update_folder`, `new_parent_id == folder_id`) — **no deeper cycle check** (e.g. moving folder A into its own grandchild). Found/confirmed live at P23-S4 (`user-ui`'s new drag-and-drop move, `apps/user-ui/src/components/FolderTree.tsx`): a client-side safeguard prevents a visible cycle only within the part of the tree currently loaded/visible (the target must not already be a loaded descendant of the dragged folder), but does **not** prevent a cycle via direct API calls or invisible tree parts — a two-node cycle forced via curl (A→B, then B→A) was confirmed possible and afterward makes BOTH folders undeletable (the not-empty check sees the other as a child in each case). Deliberately not fixed in P23-S4 (purely a frontend session scope) — a real fix would need to walk the ancestor chain of the new parent folder server-side up to the root on move and reject if the folder being moved appears in it.~~ — **fixed in Post-Roadmap Phase 38 Session 1**: `update_folder` now rejects a move whenever the intended new parent is inside the folder's own active subtree (reusing the already-existing `list_active_subtree_ids`, the same helper the trash cascade uses — the folder itself is always the first element of its own subtree, so this one check also subsumes the old, narrower self-parent guard). Re-verified live against the real running stack with the exact A→B→A reproduction named above: now rejected with `400` instead of succeeding, and both folders remain cleanly deletable afterward.
- ~~No legal-hold role check (5.2, since P7-S1b) — identical open question as in `document-service` (P7-S1)~~ — **resolved in Post-Roadmap Phase 19 Session 10** ([ADR 0075](../adr/0075-legal-hold-rbac.md)), together with `document-service`. First consumer of `libs/dms-permission-client` in this service.
- **Deletion register not differentiated by backup** (5.2a) — identical limitation as in `document-service` (Phase 11 is still missing). In `document-service` this is partially compensated via the `audit-service` hash chain (`document.>` is consumed there) — `audit-service` does not yet consume `folder.>` (a pre-existing gap, not introduced in this session), so this compensation is entirely missing here.
- **Structure templates check neither required attributes nor parent-child object-type nesting rules (2.2b) when applying** (since P15-S6, see above) — a deliberate, documented simplification (ADR 0056), no mode in `object-type-service` exists for "structure-only, no attribute check".
- **`created_by` on the template endpoints remains client-side in the body** (since P15-S6) — deliberately follows the pattern already used consistently throughout this service (`FolderCreate.created_by`, `TrashRequest.deleted_by`), not the newer `X-DMS-Principal` convention already hardened elsewhere in the project — a pre-existing legacy gap of the whole service, not resolved in this session.
- ~~**`root` itself has no rename/move/delete protection** (P15-S3, found while building the new `inbox`/`outbox` protection)~~ — **resolved in Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)): `root` is now part of `PROTECTED_FOLDER_IDS`, going through the same three existing `409` checks as `inbox`/`outbox`.
- ~~**Beyond hand folders' `folder.read`/`folder.write` (14.2, Post-Roadmap Phase 31 Session 7, ADR 0118), core folder CRUD (`POST`/`GET`/`PATCH`/`DELETE /folders/...`) still enforces essentially no permission of its own** — `created_by`/`deleted_by` remain client-supplied body fields (see above), and reading/browsing any folder's contents is ungated. A work tray built on a teamspace's root folder is therefore only as securable in practice as the teamspace's own permission-service anchoring already is — which, per `docs/services/teamspace-service.md` "Open Points", is not the primary enforcement anywhere except `search-service` today. Closing this properly is a materially larger, separate RBAC-hardening effort (same category as Phase 19), not attempted in P31-S7.~~ — **resolved in Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)): core folder CRUD now checks real `folder.read`/`folder.write` against `permission-service`'s resource tree, granted to "everyone" for ordinary folders (preserving default openness) while a teamspace's root folder is excluded via `inherit=False` — teamspace membership (ADR 0043) is now the actual, working enforcement, not just a `search-service`-only anchor. Hand folders (ADR 0118) deliberately kept their OWN, separate `folder.document_reference.read`/`.write` permission pair rather than reusing the now-broadly-granted `folder.read`/`.write`, preserving that feature's originally intended narrower gate.
- ~~**Residual, accepted gap (Post-Roadmap Phase 38 Session 4, ADR 0149)**: `teamspace-member`'s broad `folder.write` grant on a teamspace's root folder lets a regular (non-manager) member bypass `teamspace-service`'s own manager-only deletion guard via `DELETE /folders/{root_folder_id}` directly...~~ — **closed in Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)): `DELETE /folders/{id}`/`POST /folders/{id}/trash` now check a dedicated `folder.delete` permission instead of the broader `folder.write` every member already has (`_require_folder_delete_permission`, mirroring hand folders' existing "own dedicated permission pair" precedent, ADR 0118). Granted to "everyone" by default (preserving ordinary, non-teamspace delete/trash behavior unchanged); `teamspace-service` grants it additionally ONLY via its new `teamspace-manager` role, see `docs/services/teamspace-service.md`.
- ~~**`folder-service`'s own orphaned-`ResourceNode`-on-purge gap** (first named, but explicitly not fixed, in [ADR 0154](../adr/0154-document-per-document-resource-case-list-filtering-org-hierarchy-case-scope.md) while building the equivalent fix for documents): `POST /folders/{id}/purge`, the retention-poll-driven trash-expiry purge, the retention-poll-driven forced deletion, and `POST /folders/{id}/reconcile-restore-deletion` all called `retention_actions.execute_forced_deletion`/`purge_expired_trash_entry` and published only a BUSINESS event (`folder.force_deleted`/`folder.trash_purged`), never the structure-tree LIFECYCLE event `permission-service`'s `structure_consumer.py` needs to remove the corresponding `ResourceNode` - only the direct, synchronous `DELETE /folders/{id}` ever did.~~ — **closed in Phase 44 Session 2** ([ADR 0163](../adr/0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)): all four call sites now also publish `folder.resource.deleted` right after their existing business event.
