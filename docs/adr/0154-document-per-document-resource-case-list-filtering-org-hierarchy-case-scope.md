# 0154 — Document/case RBAC polish: per-document resource type, case list row-level filtering, org-hierarchy case scope

**Status:** accepted
**Context:** P39-S4 (Post-Roadmap Phase 39, concept 4.1/14.2), the fourth and final session of Phase 39
"RBAC Completion". Plan text: "documents get a real per-document resource-tree entry (like cases got
since [ADR 0144](0144-case-per-case-resource-type.md)) instead of the coarse `resource_id="root"`
fallback; `GET /cases` gets row-level RBAC filtering (currently all-or-nothing); org-hierarchy grants
([ADR 0121](0121-org-hierarchy-dynamic-access-grants.md)) narrow onto the new case resource type instead
of scoping only by `process_definition_id`." A research pass ahead of implementation (per the now very
strong pattern from P38/P39-S1/S2/S3 of stale plan premises) found items 2 and 3 real and accurately
described, but item 1's framing overstated the gap: documents already check their containing FOLDER's
real resource node (not literally `"root"` for most filed documents), materially finer than what cases
had before ADR 0144 (which always checked `"root"`, no substructure at all). No concrete use case was
found anywhere in the codebase for two documents in the same folder needing different permissions.
Presented to the user as a build-vs-skip decision; **the user chose to build it fully**, matching cases'
architecture exactly, migrating every existing document permission check to the document's own resource.

## Decision

**(1) Documents get a real per-document `ResourceNode`, mirroring ADR 0144's case pattern exactly.**
`_persist_new_document` (the single, shared creation path for uploads/derived documents/redaction
copies) synchronously calls the new `document_service.permission_client.PermissionServiceClient.
create_resource_node()` (a duplicate of `dms-permission-client`'s existing method - see "Consequences"
for why not consolidated) right after the document row commits, `parent_id=folder_id or "root"`,
`resource_type="document"`, THEN publishes `document.resource.created` for symmetry/self-healing
(harmless no-op via `structure_consumer.py`'s already-idempotent handler) - identical synchronous-plus-
event shape to `case-service`'s `create_case`, for the identical reason (an unregistered `resource_id`
denies every check outright, so the caller must never see a `document_id` before its node exists).
`permission-service/settings.py`'s `structure_subjects` gained `"document.>"` (config-only,
`structure_consumer.py`'s handler is already domain-agnostic, dispatching on event-type SUFFIX).

**Startup backfill, same self-healing idempotency as `case-service`'s own, but BOUNDED-CONCURRENT
rather than sequential** - without it, every document created BEFORE this session would become
permanently inaccessible the instant this session's `resource_id=document_id` checks went live. Live
verification against this project's own dev database (42,699 real documents, accumulated across many
prior sessions' own live verifications) found the naive sequential version of this loop actually CRASHED
document-service's startup outright (see the `ForeignKeyViolationError` finding below), and even once
that was fixed, took long enough to be a real concern. `new repository.list_all_documents()` plus a
`asyncio.Semaphore`-bounded `asyncio.gather` (default concurrency 50,
`document_resource_backfill_concurrency`) brought a clean run down to ~109 seconds in this environment -
still non-trivial for an installation this size, but a startup delay, not a crash, and unbounded
concurrency was rejected as trading one connection-pool-exhaustion risk for another (the exact class of
bug this same session's item 1 cross-service-consistency fix closed for query-service/reporting-service,
see below).

**Two real, previously-unknown robustness gaps, found only by live verification, not by the test
suite:**

1. The FIRST attempt at the backfill loop crashed document-service's entire startup with an unhandled
   `asyncpg.exceptions.ForeignKeyViolationError` - a real folder in this installation's resource tree
   had no `ResourceNode` of its own (`folder-service`'s registration is purely event-driven, ADR 0144's
   own text already contrasts this with case/document-service's synchronous guarantee), so registering
   a document under it as `parent_id` violated the FK constraint. Fixed at the source, in
   `permission_service.repository.create_resource_node()` itself (shared by every caller: the REST
   endpoint, the NATS structure-consumer, and now this backfill): if `parent_id` doesn't correspond to
   an existing node, it falls back to `root` with a logged warning instead of raising - the same
   "unregistered resource → treat gracefully" posture this whole mechanism already has everywhere else,
   now also covering a bad `parent_id` at INSERT time, not just a bad `resource_id` at CHECK time. This
   protects every caller of `create_resource_node()` against this exact class of pre-existing orphaned-
   reference data, not just this session's own new backfill loop.
2. The bounded-concurrent `asyncio.gather` still crashed the WHOLE startup on a single transient
   connection error against `permission-service` (e.g. it restarting around the same time as
   document-service, observed directly while iterating on this session's own live verification) -
   `asyncio.gather`'s default fail-fast behavior means one bad row aborted registration for every OTHER
   row too, not just that one. Fixed by wrapping each individual backfill call in its own try/except,
   logging and moving on - the loop already runs on every restart specifically so a missed row
   self-heals next time, so failing the entire startup for one transient error is a substantially worse
   outcome than leaving one row to be caught on the next pass.

**Move re-parenting**: `PATCH /documents/{id}` and `POST /documents/{id}/promote` (the two paths that
change `folder_id` on an EXISTING document) now publish `document.resource.moved` when a move actually
happens, mirroring `folder-service`'s `folder.resource.moved` on its own `PATCH /folders/{id}`.

**Hard-delete cleanup - a DELIBERATE departure from the folder/case precedent.** All five real hard-
delete call sites (`execute_forced_deletion` ×2, `purge_expired_trash_entry` ×2, `execute_quarantine_
auto_delete`) now also publish `document.resource.deleted`. Research found `folder-service` itself has a
pre-existing, undocumented gap here: `POST /folders/{id}/purge` and the automatic trash-expiry purge
never publish `folder.resource.deleted` at all, leaving orphaned `ResourceNode` rows behind on every
folder purge (only the separate, immediate `DELETE /folders/{id}` cleans up). Rather than faithfully
reproducing this KNOWN, pre-existing gap for documents (which have no direct-hard-delete endpoint at
all, only trash/purge), this session closes it for documents from day one - a small, cheap, and clearly
correct addition while building the mechanism fresh anyway. `folder-service`'s own gap is explicitly
**not** fixed here (out of this session's scope, a separate, independent fix for a future session).

**Call-site migration (~29 sites across 4 services)**: every EXISTING-document permission check in
`document-service` (get/update/register/promote/redact/share-link/webdav-edit-token/lock/version/
export/checkin, ~22 call sites) now passes the document's own `id` as `resource_id` instead of
`document.folder_id or "root"` - inheritance via the existing resource-tree climbing (`ResourceNode.
inherit`, default `True`) means every pre-existing folder-level `RoleAssignment` continues to apply
unchanged (a document's node parents onto its folder/root), while an admin can now ALSO narrow one
specific document's own `RoleAssignment`s, exactly the same guarantee ADR 0144 gave for cases. Checks
that inherently precede the document's existence (`POST /documents`) or target a DIFFERENT, destination
folder (a move's target-folder check, `POST /folders/{id}/export`'s whole-folder export) deliberately
stay folder-scoped - migrating those would be a category error, not a fix. `query-service`/`reporting-
service`/`search-service`'s own row-level result filtering (`filtering.py`/`main.py`'s `/search`) also
switched from resolving a document event/row to its folder (`document_client.get_document(...).
folder_id`) to using the document's own id directly - closing a LATENT inconsistency this session's own
change would otherwise have introduced (an admin narrowing one document's permission would have had no
effect on search/query/forensic-trace results, which would still check the coarser folder). `query-
service`/`reporting-service` no longer need a `DocumentClient` dependency for this purpose at all -
reporting-service's was used for nothing else and is removed entirely (settings/client/lifespan wiring).

**(2) `GET /cases`/`GET /cases/by-vorgangsnummer` get row-level RBAC filtering**, via a new
`_filter_cases_by_permission()` helper reusing the already-available `dms-permission-client.check_batch`
(no new client method needed) - same `check_batch`-then-filter shape as `query-service`/`reporting-
service`/`search-service`'s own established pattern. The existing collection-level `case.read`-on-`root`
baseline gate (`_require_case_permission`'s default `resource_id`) is UNCHANGED and stays in place -
row-level filtering is additive on top of it, not a replacement (a principal with zero `case.read`
anywhere, including via "everyone", still gets `403` at the door; row-level filtering only prunes
individual rows for a principal who passes that baseline but has had one specific case narrowed via its
own `ResourceNode`'s `inherit=False`/dedicated `RoleAssignment`, exactly ADR 0144's own intended use).
`GET /cases/due-for-archival` (the internal, ungated, no-principal machine-to-machine callback) is
deliberately untouched - there is no principal to filter by.

**(3) Org-hierarchy grants gain a fourth delegation scope dimension, `scope_case_resource_ids`**, on the
`Delegation` model/`_delegation_scope_matches`/`is_active_deputy_for`/`GET /delegations/check` - same
fail-closed shape as the pre-existing three (`scope_object_type_ids`/`scope_process_definition_ids`/
`scope_folder_resource_ids`), all set dimensions must match. `workflow-service`'s `_resolve_business_key_
scope()` (already resolving `business_key` → case/document via ADR 0131's cross-service lookup) now ALSO
returns the resolved case's own id as `case_resource_id` when `business_key` resolves to a real case -
no extra case-service field needed, since a case's `resource_id` IS its own `id` (ADR 0144). `POST
/instances/{id}/tasks/{id}/org-hierarchy-grant` resolves this and passes it through
`create_org_hierarchy_grant(case_resource_id=...)`, which sets `scope_case_resource_ids=[case_resource_
id]` IN ADDITION TO the pre-existing `scope_process_definition_ids=[process_definition_id]` (both
dimensions must match - strictly narrower than the process-definition family alone, e.g. "every Bauantrag
case" narrows to "this one Bauantrag case"). Also exposed on self-service `POST /delegations`
(`DelegationCreate`/`DelegationOut`) for consistency - the underlying storage/matching is shared, and
leaving it settable only via org-hierarchy grants while storing it in the same generic `Delegation` row
would be an inconsistent half-feature. `_require_delegation_if_on_behalf_of`'s existing check call also
threads the new dimension through, since a grant with `scope_case_resource_ids` set would otherwise
always fail-closed against a check that never supplies it.

## Rationale

- **Full build, per explicit user choice, despite the weaker use-case justification than cases had**:
  documents already had folder-granularity (materially finer than cases' pre-ADR-0144 root-only state),
  so there was no urgent driver - but consistency with cases' architecture, admin-editability down to a
  single document, and closing the exact gap the plan named all favored building it now rather than
  deferring further.
- **Mirroring case-service's exact synchronous-registration/backfill pattern instead of inventing a new
  one**: ADR 0144 already solved the identical "avoid a race window where a freshly created resource is
  briefly unreadable" and "avoid permanently orphaning pre-existing rows" problems; reusing the same
  shape is lower-risk than a novel design and keeps the two resource types' operational behavior
  consistent for whoever operates this system.
- **Closing folder-service's orphaned-resource-on-purge gap for documents, but not fixing it for folders
  themselves**: building the mechanism fresh made the fix nearly free for documents; retrofitting
  folder-service's own purge paths is a separate, independently-scoped piece of work with its own blast
  radius (`folder-service`'s trash/retention poll loop), not attempted here to keep this already-large
  session's scope bounded to what was asked. **Closed in Phase 44 Session 2**
  ([ADR 0163](0163-teamspace-folder-delete-permission-and-orphan-resource-cleanup.md)) — all four of
  `folder-service`'s real hard-delete call sites now also publish `folder.resource.deleted`.
- **Migrating query-service/reporting-service/search-service's row-level filtering, even though the plan
  didn't explicitly name them**: leaving them checking the folder while document-service itself checks
  the document would have been an internal inconsistency INTRODUCED by this session's own item 1, not a
  pre-existing, independently-scoped gap - fixing it is part of doing item 1 correctly, not scope creep.
- **Case-list filtering as ADDITIVE, not a replacement of the coarse gate**: removing the baseline check
  entirely would change `GET /cases`' behavior for every principal with literally zero `case.read`
  anywhere (200 + empty list instead of `403`) - a broader behavioral change than "add filtering" calls
  for, and the existing `403`-on-zero-access test's premise remains valid and correct after this session.
- **Case resource_id needs no new case-service field**: `business_key=case_id` is already an established
  convention (`case_service.workflow_client`), and cases are already real `ResourceNode`s keyed by their
  own id (ADR 0144) - the resolution only needed a third return value, not new API surface.

## Consequences

- **`document-service`'s `PermissionServiceClient` gained a duplicated `create_resource_node()` method**
  instead of migrating the service onto the shared `dms-permission-client` package - a separate,
  independent piece of tech debt (this service's client predates the Phase 19 Session 1 consolidation
  that created the shared package) not attempted in this session, to keep the change minimal and
  targeted at exactly what was asked.
- **`reporting-service` no longer depends on `document-service` at all** (its `DocumentClient`/
  `document_service_base_url` setting/`app.state.document_client` removed entirely, having served no
  purpose beyond the now-removed folder-resolution lookup) - a net simplification, not a regression.
- ~~**Every document now carries a `ResourceNode` row**, doubling permission-service's resource-tree size
  in installations with large document counts. **Measured, not theoretical**: this session's own dev
  database (42,699 documents) made a first, naive sequential backfill crash the service outright (see
  above) and, even bounded-concurrent at 50, adds roughly 109 seconds to every `document-service`
  startup in this environment - a real, ongoing cost for an installation at this scale, not just a
  one-time migration cost, since the loop runs on every restart (self-healing, matching `case-service`'s
  own precedent). A future session should reconsider "run on every startup" if this becomes disruptive
  in practice (e.g. a persisted "backfill completed" marker to skip it once caught up, or a genuine bulk
  registration endpoint on `permission-service` instead of one HTTP round trip per document) - not
  addressed here, since it was discovered during this session's own live verification, not before.~~ —
  **closed in Phase 53 Session 2**, using exactly the first option this bullet itself floated: a
  persisted `ResourceBackfillState` marker (single-row, same pattern as `TrashConfig`/other singleton
  config tables), set only after a pass over every document completes with zero failures. All
  subsequent startups skip the scan+fan-out entirely instead of repeating it, while a genuinely
  incomplete installation (any failure in a pass) still retries on the next restart exactly as before -
  the self-healing property this ADR names is unchanged, only a fully-caught-up installation's redundant
  work is eliminated. See `docs/services/document-service.md` for the mechanism.
- **`GET /cases`/`.../by-vorgangsnummer` now make one additional `check_batch` call per request** beyond
  the pre-existing baseline check - an accepted cost, matching the same trade-off `query-service`/
  `reporting-service`/`search-service`'s already-established row-level filtering already makes.
- ~~**A fourth delegation scope dimension exists but has no dedicated UI field** - `POST /delegations`'s
  schema accepts `scope_case_resource_ids`, but `user-ui`'s `DelegationsPane` (self-service delegation
  form) does not expose ANY scope dimension today, not even the three pre-existing ones
  (`scope_object_type_ids`/`scope_process_definition_ids`/`scope_folder_resource_ids`) - a person can
  only pick a deputy and a time window, never a scope, via the UI. This is a pre-existing gap this
  session does not close (org-hierarchy grants populate the new dimension automatically without any UI
  change needed, since they're system-triggered, not user-authored). A future session could add scope
  selection to `DelegationsPane` for all four dimensions at once, not case-scoping in isolation.~~ —
  **closed in Phase 53 Session 2.** This bullet's own claim was already imprecise when written: two of
  the three "pre-existing" dimensions (`scope_object_type_ids`/`scope_folder_resource_ids`) already had
  UI since **Post-Roadmap Phase 32 Session 2** ([ADR 0131](0131-delegation-scope-resolution-case-then-document.md)),
  predating this ADR - only `scope_process_definition_ids` and `scope_case_resource_ids` were genuinely
  missing. This session adds a `scope_case_resource_ids` field to `DelegationsPane.tsx`, matching the
  existing `scope_folder_resource_ids` field's exact idiom (comma-separated free text - no case-picker
  component exists anywhere in `user-ui`, and case counts are comparable in scale to documents, so a
  `<select>` wouldn't scale the way it does for the much smaller object-type list). `scope_process_
  definition_ids` remains genuinely missing from the UI - out of this session's stated scope
  (case-scoping specifically), not silently closed alongside it.
