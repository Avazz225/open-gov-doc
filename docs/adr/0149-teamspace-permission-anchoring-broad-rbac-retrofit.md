# 0149 — Teamspace permission anchoring via a broad `document`/`folder` RBAC retrofit

**Status:** accepted (P38-S4, see Phase 38 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 38 Session 4 (security/correctness bugfixes), affects `permission-service`,
`folder-service`, `document-service`, `teamspace-service`, `libs/dms-connector-sdk`,
`webdav-connector`, `cmis-connector`, `migration-service`, and ~10 other services' internal
document-service/folder-service HTTP clients

## Decision

The plan's premise for this session was stale (the same pattern already seen at P38-S1/S3): it asked to
"design and build" a mechanism anchoring teamspace membership into `permission-service`'s resource-tree
RBAC. That mechanism **already exists**, since ADR 0043 (P14-S6) — `teamspace-service` already grants a
real `teamspace-member` role (`document.read`/`document.write`/`folder.read`/`folder.write`) on a
teamspace's root folder to every member at invite/creation/removal time. The actual gap, confirmed by
research: `folder-service`'s core CRUD calls `permission-service` **never**, and `document-service` only
calls it on a narrow "sensitive" subset of endpoints (share-links, webdav-edit-tokens, redaction, exports)
— not the primary read/write paths (`GET /documents/{id}`, `GET /documents/{id}/content`, `PATCH`, etc.).
Direct access via either service therefore bypasses teamspace membership entirely.

Presented with two options — (1) a narrow, teamspace-scoped-only enforcement, or (2) a broad retrofit of
real `document.read`/`.write`/`folder.read`/`.write` checks across both services' core paths — **the user
explicitly chose option (2)**, the larger, riskier one, against the recommendation. This session implements
that broad retrofit and everything it required to avoid a system-breaking regression.

### The core mechanism: "everyone" + `inherit=False`

Retrofitting a real `document.read`/`.write`/`folder.read`/`.write` check onto every core CRUD endpoint,
without also making these permissions available by default, would 403 every ordinary, non-teamspace,
non-shared document/folder for everyone except its literal creator — there is no "creator gets an
automatic grant" mechanism in this project. Fix: add all four permissions to `permission-service`'s
`EVERYONE_ROLE_PERMISSIONS` (`repository.py`), so every authenticated principal keeps the previous
de-facto-open behavior for ordinary resources (the only change: a valid `X-DMS-Principal` is now required,
401 instead of no check at all). A teamspace's root folder is excluded from this via `inherit=False` on its
own `ResourceNode`: `permission-service`'s ancestor walk (`_collect_effective_roles`) evaluates a node's
own role assignments before stopping the ascent when `inherit=False` — so a non-member walking up from
inside a teamspace stops at the teamspace root (seeing only real member grants there, none), while a member
is granted at exactly that node via their own `teamspace-member` assignment.

`teamspace-service`'s `PermissionServiceClient` gained `ensure_isolated_resource` (called from
`create_teamspace`, before `grant_resource_access`): `POST /resources` (synchronous, idempotent
create-if-missing, ADR 0144's own primitive) followed by `PATCH /resources/{id}` with `inherit=False` (both
endpoints already existed and are deliberately ungated, same trust boundary as the NATS event bus). A
startup-idempotent backfill (`_ensure_teamspace_isolation_backfill` in `main.py`, using the new
`list_all_root_folder_ids` repository function) applies the same to every teamspace created before this
session, whose root folder's `ResourceNode` arrived via the older async `folder.resource.created` event with
`inherit` defaulting to `True`.

`folder-service`'s `inbox`/`outbox` special folders needed the same `POST /resources` registration at
startup: unlike ordinary folders (registered via the `folder.resource.created` event on `create_folder`),
they are bootstrapped directly into the DB (`ensure_special_folders`) and never went through that event —
before this session that silently didn't matter (no check existed to care), but an unregistered `resource_id`
now denies outright (`_collect_effective_roles` breaks its ancestor walk immediately if the node doesn't
exist), so `GET /folders/inbox` would otherwise 403 for everyone.

### A second, more serious collision: pre-existing narrow gates reusing the same two permission strings

Several **pre-existing** endpoints — hand-folder curation (`_require_folder_document_reference_permission`,
ADR 0118), share-link creation/listing (ADR 0047), webdav-edit-token creation/listing, redaction
(page-count/page-image/redact), and export (accessibility-check/export/folder-export, ADR 0107) — already
checked `folder.read`/`folder.write` or `document.read`/`document.write` as their OWN, deliberately narrow
gate, built at a time when these were the only real checks anywhere in either service. ADR 0118 says this
explicitly and in so many words: *"`folder.read`/`folder.write` are new, not-yet-open permission strings...
an installation must explicitly grant them per hand folder... before anyone besides the creator can
curate/view one. This is the intended, deliberate behavior, not a gap to close later."* ADR 0047 similarly
frames the share-link check as deserving real enforcement specifically *because* issuing an anonymous,
public, internet-reachable link is "qualitatively a bigger security step than yet another internal,
already-authenticated API call."

Adding these two permissions to "everyone" (required for the broad retrofit above) would have silently
defeated every one of these pre-existing gates: since "everyone" now holds `document.read`/`.write`/
`folder.read`/`.write` on any ordinary resource, literally any authenticated principal would gain hand-folder
curation rights, the ability to mint a public anonymous share link, direct-edit access via WebDAV, and
redaction/export access to any document — a real, material widening of exposure, not merely a side effect.

Fix: each of these five domains now checks its own dedicated permission string, distinct from the new
baseline:
- `folder.document_reference.read`/`.write` (hand folders, `folder-service`)
- `document.share_link.read` (share-link create/list)
- `document.webdav_edit.write` (webdav-edit-token create/list)
- `document.redaction.read` (redaction-preview page-count/page-image, redact)
- `document.export.read` (accessibility-check, single-document export, folder export)

`document-service`'s `permission_client.py`'s `check_read`/`check_write` gained a `permission: str =` kwarg
(defaulting to `document.read`/`document.write`, i.e. unchanged for the new baseline gate); each of the
above call sites now passes its own string. None of these five are added to `EVERYONE_ROLE_PERMISSIONS` —
an installation must still explicitly grant them, exactly as before. A real installation with an existing
per-resource grant on the OLD `folder.read`/`document.read`/`document.write` strings for one of these
features needs to be re-granted under the new names; no admin-ui surface exists for any of these five today
(all API-only, per their own ADRs' "Consequences" sections), so the blast radius is limited to whoever set
one up by hand via `permission-service`'s API directly.

### Internal service-to-service callers

An audit found ~13 other services with internal HTTP clients calling folder-service's/document-service's
now-gated endpoints with no principal header at all (would 401): `archival-service`, `ocr-service`,
`rendering-service`, `signature-service`, `mail-connector`, `search-service`, `query-service`,
`reporting-service`, `case-service`, `workflow-service`, plus the shared `libs/dms-connector-sdk`
`DmsTreeClient` (used by `webdav-connector`/`cmis-connector`/`migration-service`), and
`teamspace-service`'s own `FolderServiceClient.create_folder`.

Two different fixes, by nature of the caller:
- **Pure background/system callers** (no real end-user context) got a **fixed service-identity**
  `X-DMS-Principal` header on their internal client, mirroring the pattern `archival-service` already used
  for its own `mark_archived`/`mark_dehydrated`/`mark_rehydrated` calls (e.g. `"ocr-service"`,
  `"rendering-service"`, `"search-service"`, `"teamspace-service"`, `"migration-service"`). This works
  because "everyone" now covers ordinary resources — a system identity reaches everything a background job
  is expected to reach, except a teamspace-scoped resource, which is an accepted, documented limitation for
  these callers (none of them act on behalf of a specific end user for this purpose).
- **`webdav-connector`/`cmis-connector`** already resolve a REAL per-request actor for other purposes
  (`created_by`/`deleted_by`/`locked_by` fields, via `_actor(environ)`/`require_actor`) — `DmsTreeClient`
  gained an `x_dms_principal` parameter on every method (plus a `default_principal` constructor default for
  callers with no such context, used by `migration-service`), and both connectors now forward that real
  actor identity on every call instead of a fixed identity. This is the more correct choice for a connector
  serving real, distinct end users: a fixed identity would not be a teamspace member and would incorrectly
  block legitimate access to teamspace-backed content through WebDAV/CMIS, whereas the real caller's
  principal resolves exactly like any other permission check in this session — this is also what actually
  makes teamspace enforcement work end-to-end through these two connectors, not just through direct API
  access.

`document-service`'s own internal `folder_client.get()` calls (parent-folder existence/placement
validation inside `create_document`/`update_document`/`promote_document`) were changed the same way as the
connectors: forwarding the real caller's own `x_dms_principal` rather than a fixed identity, for the same
reason (a fixed service identity would incorrectly block legitimate moves into a teamspace folder the real
caller is a member of).

### A live consequence found and fixed: `delete_teamspace` permanently orphaning its kept folder

`delete_teamspace` deliberately deletes only the teamspace metadata and keeps the root folder (a
pre-existing, documented decision, not revisited here) — but before this session that kept folder was
harmless clutter (nothing enforced anything on it). Now, with `inherit=False` plus every member's grant
revoked on deletion, that kept folder becomes **permanently unreachable by anyone, including its creator**,
with no UI trace pointing back to it. Fixed by adding `PermissionServiceClient.restore_default_inheritance`
(`PATCH /resources/{id}` with `inherit: true`), called from `delete_teamspace` right after revoking member
access — the kept folder now reverts to being an ordinary, "everyone"-accessible folder under `root`, exactly
as intended by the original "the folder remains" design, instead of a sealed dead end.

### A live consequence found and fixed: `teamspace-service`'s own test suite leaking real state

`teamspace-service`'s test suite deliberately runs against the real, live `folder-service`/
`permission-service` containers (no mocking, per its own module docstring) — so its tests' calls to
`create_teamspace` create REAL folders and REAL `inherit=False` resource nodes in whatever database those
neighbor containers are actually pointed at. `teamspace-service`'s own DB-level test isolation
(`TEST_POSTGRES_DSN` → `dms_test`) correctly cleans up the `Teamspace` row after each test, but that row was
never the only trace of the created folder — the folder-service folder and its permission-service resource
node live outside that isolation boundary entirely and were never cleaned up. Before this session that was
harmless clutter (found live: ~1039 leftover folders and 236 leftover documents accumulated directly under
`root` in the shared dev database from many past sessions' test runs and live-verification steps — with no
functional impact, since nothing enforced anything on them). This session's own retrofit turned a subset of
that clutter (the ones with `inherit=False` from teamspace-service's tests, which this session's new backfill
and `ensure_isolated_resource` code path is what actually engages) into genuinely inaccessible dead folders,
and additionally made ordinary root browsing measurably slow enough to exceed test client timeouts (`webdav-
connector`'s `DmsTreeClient.list_children` makes one extra HTTP call per document to enrich version metadata,
a documented, accepted tradeoff for a reference implementation — with ~236 documents piled up under `root`
this pushed webdav-connector's own root-listing test past its httpx client's default 5s timeout).

Fixed two ways: (1) a one-time cleanup purged the accumulated clutter from the live dev database (236
documents, 1039 folders, via each service's own DELETE/purge endpoints — not raw SQL — plus resetting
`inherit` on the ~170 folders left permanently locked by past `inherit=False` grants with no member left to
unlock them); (2) `teamspace-service`'s test suite now tracks `(root_folder_id, principal)` for every
teamspace it creates and purges the created folder via `folder-service` in an autouse teardown fixture, using
the same principal that created it (a separate cleanup identity would itself be blocked by `inherit=False`) —
so this specific leak does not recur on every future test run. `webdav-connector`'s test suite additionally
got its httpx/webdav4 client timeouts bumped from the 5s default to `30.0` (matching `DmsTreeClient`'s own
internal clients), since a legitimate root listing over a busier real installation can reasonably take longer
than 5 seconds and a reference implementation's O(document count) enrichment loop is an accepted, documented
tradeoff, not something this session's scope calls for optimizing.

## Rationale

- **Why not scope enforcement to teamspace resources only** (the recommended, narrower option): the user
  explicitly chose the broader option after being shown the tradeoff and the accepted blast radius. Given
  that choice, the design above is the way to retrofit broad enforcement without a system-breaking
  regression — narrower scoping would have been simpler but was not what was asked for.
- **Why "everyone" instead of some other default-grant mechanism**: this is the established, existing
  pattern for exactly this situation in this project (ADR 0067 P19-S2, extended repeatedly since for
  `case.read`/`.write`, `archival.*`, `reporting.*`, `ocr.*`, `rendering.*`, `workflow.write`,
  `virus_scan.*`, `audit.read`) — every prior instance of "a service had zero enforcement, now needs real
  enforcement without breaking existing open access" used exactly this mechanism. Reusing it is consistency,
  not a new pattern.
- **Why dedicated permission strings for the five pre-existing narrow gates, not a shared "elevated"
  permission**: keeping them separate preserves each ADR's own documented granularity (an admin could
  already grant, say, only hand-folder curation without also granting export rights) and requires zero
  changes to how `permission-service` itself works — only which string each call site checks.
- **Why fix `delete_teamspace`'s orphaning consequence rather than just documenting it**: unlike the
  residual noted below (a genuine, accepted tradeoff), a permanently unreachable folder with zero possible
  recovery path (not even for its own creator, not even via a direct admin grant, since nothing points back
  to it) is a straightforward, low-risk, clearly-scoped fix directly caused by this session's own new
  mechanism — not fixing it would leave a footgun for every future teamspace deletion.
- **Why clean up test-suite state and accumulated dev-database clutter in this session rather than deferring
  it**: the accumulated clutter is genuinely unrelated to this session's changes (present for a long time,
  harmless until now), but this session is what makes it harmful, and the fix (an autouse teardown fixture)
  is small, obviously correct, and prevents the exact same problem from recurring on every future test run
  of a file this session already had to modify extensively.

## Consequences

- **Residual, accepted gap**: `teamspace-member`'s broad `folder.write` grant on a teamspace's root folder
  means a regular (non-manager) member can now bypass `teamspace-service`'s own manager-only deletion guard
  by calling `folder-service`'s `DELETE /folders/{root_folder_id}` directly instead of going through
  `teamspace-service`. This is a **net improvement** over the prior fully-open state (previously *anyone*,
  member or not, could do this via the totally-ungated `folder-service` endpoint) — not a new regression —
  but it is not closed by this session. Closing it would need folder-service to distinguish "member with
  ordinary write access" from "member with delete/admin rights on this specific folder," which ADR 0043's
  single-role model does not currently support.
- **Residual, accepted gap**: a soft-deleted (trashed) document whose parent folder is later hard-deleted
  becomes permanently unreadable by anyone, including via `GET /documents/{id}/deleted`-family admin/trash
  views — hard-deleting a folder removes its `ResourceNode` in `permission-service` (a pre-existing
  mechanism, `folder.resource.deleted`, unrelated to this session), and `document.folder_id` still points
  at that now-nonexistent resource, so the ancestor walk finds nothing to check against for ANY principal.
  Found live via `cmis-connector`'s `deleteTree` test. Before this session this was harmless (nothing
  checked it); now it's a real, if narrow, dead end. Not fixed here — would need either teaching the
  permission check to special-case "no resource node" for already-deleted documents, or having folder
  deletion also touch cross-service document rows it does not otherwise know about; both are more
  invasive design changes than this session's scope calls for.
- **Deliberately left ungated** (internal/server-to-server or status-visibility endpoints, matching this
  project's existing precedent elsewhere for "network-topology-trust" paths): `document-service`'s
  `cascade-trash`/`cascade-restore` (called only by `folder-service` during folder cascade operations),
  `count_active_by_folder_ids`/`count_active_total` (pure counts, no content), `archive-request`/
  `archive-status`/`has-active-hold`/`has-active-quarantine` (pre-existing deliberate ungating), and
  `list_documents_by_kennzeichen` (cross-folder global search used by `mail-connector`; row-level filtering
  here would be a separate, larger effort, out of scope).
- **Test fixtures across ~15 services** needed a default `X-DMS-Principal` header added (either at the
  `TestClient`/`httpx.Client` construction level, or per-call), following the same established pattern
  already used by `ocr-service`/`object-type-service`'s test clients before this session.
- Existing "without_principal"/"requires_permission" negative tests that relied on a bare `TestClient(app)`
  sending no header at all needed an explicit `headers={"X-DMS-Principal": ""}` override once their fixture
  gained a default — httpx overrides a same-key header per-call, so this is a minimal, surgical change per
  test, not a fixture redesign.
- `docs/services/folder-service.md`, `docs/services/document-service.md`, `docs/services/teamspace-service.md`,
  and `docs/architecture.md` needed their "Open Points"/authorization sections updated to strike the
  now-closed gaps this session addresses (see each file's own changelog for specifics).
