# cmis-connector

**Purpose:** Second reference connector of the connector architecture (Concept 3.3, P12-S4) —
makes `folder-service`/`document-service` addressable via a self-implemented **CMIS 1.1 Browser
Binding** (Chapter 5 of the OASIS CMIS 1.1 specification). The DMS acts as the
CMIS **server** here (same directional decision as `webdav-connector`, ADR 0033/0002, Concept 4.2).
First reference connector: [`webdav-connector`](webdav-connector.md) (P12-S1).

**Concept reference:** 3.3, 4.2
**No own Postgres schema** (stateless — every request translates live into HTTP calls
against `folder-service`/`document-service`, via the same `libs/dms-connector-sdk` as
`webdav-connector`)
**ADR:** [0036 — Hand-implemented Browser Binding instead of a library](../adr/0036-cmis-connector-from-scratch-browser-binding.md)

## Why hand-implemented (no library)

Unlike WebDAV (`wsgidav`, actively maintained), there is **no maintained Python CMIS *server***
library — actual research (P12-S4 kickoff) found: all notable Python CMIS packages
(`cmislib`, `cmislib3`, `cmislib-maykin`, `CMIS.PythonLib`, `drc-cmis`) are pure **client**
libraries, most of them unchanged for years. The only real server framework (Apache Chemistry's
OpenCMIS) is Java-only, with no Python equivalent. See ADR 0036 for details/reasoning.

## Scope (reference implementation, not a complete CMIS 1.1 server)

| Category | Implemented | Deliberately not implemented |
|---|---|---|
| Repository | `repositoryInfo` (service URL + repository URL) | Multi-repository (always exactly one repository, `default`), `typeChildren`/`typeDescendants`/`typeDefinition` (no CMIS type system for custom object types) |
| Navigation | `children`, `object` (by id or path) | `descendants`, `folderTree`, `parent(s)`, checked-out list |
| Reading objects | `content` (content stream) | `renditions`, `allowableActions`, `relationships`, `policies`, `acl` |
| Writing objects | `createDocument`, `createFolder`, `update` (only `cmis:name`), `move`, `delete`, `deleteTree`, `setContent` | `createDocumentFromSource`, `createRelationship`/`createPolicy`/`createItem`, `appendContent`, `deleteContent`, `addObjectToFolder`/`removeObjectFromFolder`, `applyPolicy`/`applyACL`, `bulkUpdate` |
| Versioning | `checkOut`, `cancelCheckOut`, `checkIn` (always with new content) | Full version history/`getAllVersions`, contentless checkin |
| Search | — | CMIS query language (`query` selector/action) — Concept 3.7/ADR 0012 relies on Postgres FTS for actual full-text search anyway, not CMIS SQL |

Roughly 14 endpoints in total (repositoryInfo/children/object/content for reads, the ten named
actions for writes) — comparable in scope to the WebDAV core method set from P12-S1, but without
a `wsgidav` equivalent to lean on (see ADR 0036).

## Object URL resolution (5.3.4)

An object is addressed either via the `objectId` query parameter OR via a path appended to the
root folder URL (`objectId` takes precedence, verbatim from the specification). For **write
actions** there is additionally the form control `objectId` (5.4.4.3.3) — for `update`/
`move`/`delete`/`setContent`/`checkOut`/`cancelCheckOut`/`checkIn` this addresses the object to be
edited, while for `createDocument`/`createFolder` (which have no such control) the URL itself
(query `objectId` or path) still determines the target folder. `cmis_connector.resolve` maps
both cases uniformly; a **real bug found during live verification**: the
POST routes initially read `objectId` only from the form, never from the query string —
`createDocument` therefore always ended up in the root folder, regardless of the addressed target
folder. Fix: both sources are read, with the form control taking precedence if both are present.

## No separate private working copy object

`document-service` has no concept of a "working copy" as a standalone entity — the actual
`document-service` lock (4.2) takes on exactly the role CMIS assigns to the PWC mechanism
(no write access by others until checkin/cancelCheckout). `checkOut` therefore calls
`acquire_lock()` on the original document, `checkIn`/`cancelCheckOut` correspondingly call
`write_document()`+`release_lock()`/`release_lock()` — the returned "PWC" object id is
deliberately **identical** to the original document id (real CMIS servers return a distinct id
here, but this DMS has no second object that could carry such an id). A checkout conflict
(a second actor trying to check out an already checked-out document) is detected via
`document-service`'s existing `LockConflictError` and reported as a CMIS `updateConflict` (409) —
**important**: the lock check compares `locked_by` (the actor), not `session_id`, so the same
actor can always re-"acquire" their own lock at any time (idempotent), and only a DIFFERENT actor
triggers a genuine conflict.

## `delete` on a non-empty folder (a real finding, since closed at the source)

`folder-service`'s hard-delete endpoint (`DELETE /folders/{id}`) used to only check its own
subfolders for emptiness, **not** documents — documents live in a completely different
service/schema (`document-service`) and were never cross-checked there. This was never visible
before because the only prior caller (`webdav-connector`'s `DmsDavFolder.handle_delete()`) always
recursively deletes all children first before deleting the (then guaranteed empty) folder itself —
CMIS's `delete` action, by contrast, is per the specification a **non-cascading** single-object
delete attempt that MUST fail with `constraint` (409) on a non-empty folder, so `cmis-connector`
checks itself (`_tree.list_children()`) for subfolders AND documents before calling
`delete_folder()` at all.

~~No change to `folder-service` needed, the check belongs here (a consequence of the CMIS contract,
not a general shortcoming of `folder-service`'s hard-delete fallback).~~ — **Revised in Phase 51
Session 3**: this framing undersold it — the underlying gap was a genuine orphaning risk on
`folder-service`'s own endpoint regardless of caller (ANY direct `DELETE /folders/{id}` call, CMIS
or not, could silently orphan documents), not merely a CMIS-contract nuance this connector alone
needed to work around. `folder-service` now also rejects (`409`) a folder with active documents
server-side, the same `document_client.count_active` check the forced-deletion path already used —
see `docs/services/folder-service.md`. `cmis-connector`'s own check here stays regardless: it's what
actually produces the CMIS-shaped `constraint` error rather than a raw, un-translated `409`.

## Only `cmis:name` as a writable property

This reference implementation maps only `cmis:name` (renaming) to a DMS attribute. Custom
object-type attributes (2.2, `object-type-service`) are not exposed as CMIS properties — that
would require a full CMIS type system (`typeDefinition` per object type, property definitions
with CMIS data types), see "Scope" above.

## Authentication

HTTP Basic Auth (5.2.9.1 "Basic Authentication for Non-Browser Clients") against `auth-service`'s
existing `POST /login` — identical pattern to `webdav-connector`'s `DmsAuthDomainController`,
here as a FastAPI dependency (`cmis_connector.auth.parse_basic_auth`/`require_actor`) instead of
a wsgidav `BaseDomainController`. Missing `Authorization` header → `401` with
`WWW-Authenticate: Basic` (challenge, enabling real CMIS clients to show a normal Basic Auth
dialog); wrong credentials → `403` (prevents browser login popups on accidental
browser access, explicitly cited by 5.2.9.1 as a permitted alternative).

**Authorization now actually reaches folder-service/document-service too (Post-Roadmap Phase 38
Session 4, [ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md))**: the
resolved `actor` (from `require_actor`, already used for `created_by`/`deleted_by`/`locked_by`
business fields) is now also forwarded as `x_dms_principal` on every `DmsTreeClient`/
`resolve_object`/`compute_folder_path` call, the same treatment `webdav-connector` got in the same
session. This makes teamspace membership real through CMIS too — a non-member genuinely cannot
browse/read/write a teamspace's folder via the CMIS Browser Binding.

## `asyncio.to_thread()` for the synchronous `DmsTreeClient`

Read endpoints are ordinary (non-`async`) FastAPI routes — `DmsTreeClient` is synchronous (see
`libs/dms-connector-sdk/README.md`), and FastAPI automatically runs such routes in its own
threadpool (as already the case for `webdav-connector`, ADR 0033). Write endpoints, however, MUST
be `async def` (`await request.form()` is a Starlette `async`-only API that streams the
multipart body) — the actual `DmsTreeClient` call therefore runs via
`asyncio.to_thread()` instead of blocking directly in the event loop thread (the approach
recorded in ADR 0034 as a future precedent for exactly this case).

## Extensions to `libs/dms-connector-sdk` (shared with `webdav-connector`)

- `TreeFolder`/`TreeDocument` gained `created_by`/`created_at` (both fields already existed in the
  underlying `FolderOut`/`DocumentOut` responses, but had never been carried over into the
  dataclasses) — basis for CMIS's `cmis:createdBy`/`cmis:creationDate`. For `TreeFolder`
  deliberately `str | None`/`datetime | None`: `resolve_path("")` (root addressing, traversed on
  **every** WebDAV request to the root) still constructs purely locally without an
  HTTP call — an extra round trip there would be a noticeable performance regression, `None`
  is CMIS's own "value not set" state (5.2.7), not a made-up value.
- `write_document()` gained an optional `comment` argument, passed through to
  `POST /documents/{id}/versions`'s already-existing `comment` form field — basis for
  CMIS's `checkinComment`.
- **Real performance finding during live verification, not a code bug**: `webdav-connector`'s
  test suite failed in the full `--build` regression run with `httpx.ReadTimeout` on
  PROPFIND against the root — caused by test data accumulated over the project's very
  long runtime directly under `root` (68 folders + 74 documents, from multiple connectors'/
  services' own test runs that never clean up after themselves). `DmsTreeClient.list_children()`
  deliberately makes an extra HTTP call per document (see its docstring) — with 74 documents
  directly at the root, a single request took over 10 seconds. Fixed by a one-time
  cleanup (`POST /folders/{id}/trash`/`DELETE /documents/{id}` for all 142 root objects,
  identifiable without exception by test actor names), no code change needed — reduced the same
  request to 76ms. See ADR 0036 for details. **This recurred at a much larger scale in
  Post-Roadmap Phase 38 Session 4** (236 documents/1039 folders, same underlying cause) — that
  session traced it to a specific, recurring root cause (`teamspace-service`'s own test suite
  leaking real folder-service folders on every run, since it deliberately tests against the
  real neighbor services) and fixed the actual source with a teardown fixture, not just another
  one-off cleanup — see [ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md).
  `x_dms_principal` (below) was added to `DmsTreeClient` in the same session. **The underlying N+1
  itself (`list_children()`'s "extra HTTP call per document") is fixed as of Phase 50 Session 4** —
  a new batch endpoint on `document-service` (`POST /documents/versions/current/batch`) replaces the
  per-document loop with one call per folder listing; `cmis-connector` gets the fix automatically via
  the shared `dms-connector-sdk`, confirmed via its own test suite (still-open, unrelated finding: see
  the Phase 50 Session 1 note above about `test_delete_tree_cascades_documents_and_subfolders`). See
  `docs/services/webdav-connector.md`'s Open Points for the full writeup.

## Licensing (3.3/9.1, P9-S2 pattern)

Concept 9.1 explicitly names "CMIS Connector" as an example of a separately licensable
component. `registry-service`'s `licensable_components` contains `"cmis-connector": "demo"`
(identical pattern to `webdav-connector`/`migration-service`) — demo mode only blocks
write actions, `unlicensed` blocks all access.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | `document-service` address |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | `folder-service` address |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | For the Basic Auth check |
| `DMS_CMIS_ROOT_FOLDER_ID` | `root` | DMS folder that appears as the CMIS root folder |
| `DMS_CMIS_REPOSITORY_ID` | `default` | Repository id (always exactly one repository) |
| `CMIS_CONNECTOR_PORT` | `8030` | Host port in the dev compose stack |

Example call (browser URL pattern): `http://localhost:8030/browser/default/root?cmisselector=children`.

## Tests

Runs like `webdav-connector`/`migration-service` against the real, running container (no
in-process `TestClient`, no mocking of neighboring services) — `real_user`/`second_real_user`
fixtures create real `auth-service` accounts (the latter for the checkout conflict test, which
needs two different actors). Covers: Basic Auth challenge/rejection, repository info,
children listing, object-by-id, content stream (including default selector), renaming, moving,
`setContent` versioning, a full checkout→checkin cycle, checkout conflict,
cancelCheckout, deletion (document, non-empty folder → `constraint`), cascading
`deleteTree`.

~~**Found during Phase 50 Session 1's full-repo backend regression, not fixed here (out of scope)**:
`test_delete_tree_cascades_documents_and_subfolders` now fails — `GET /documents/{id}` for a document
whose folder's `ResourceNode` was removed by the cascading `deleteTree` returns `200` where the test
asserts the documented residual `403`... Worth a dedicated session to determine whether
`document-service`'s ancestor-walk permission check now defaults to allow instead of deny when it finds
a missing/orphaned resource node — if so, this is a real authorization regression, not just a stale test
assertion.~~ — **closed in Phase 51 Session 1**: a real authorization regression, confirmed, not a stale
test assertion. Root cause was in `permission-service`, not `document-service`: `ON DELETE CASCADE` was
missing on `resource_node.parent_id`, so a folder hard-deleted while its still-trashed document's own
per-document `ResourceNode` (ADR 0154) still referenced it as parent made the deletion handler fail with
an unhandled `ForeignKeyViolationError` on every delivery attempt — the folder's node dangled forever
(not just during a race window), so the ancestor walk kept finding it and inheriting `root`'s "everyone"
grant. Fixed at the database level; see `docs/services/permission-service.md`'s "Structure
Synchronization" section and ADR 0149's updated Consequences for the full mechanism. `17`/`17` tests
passing again (was 16 passed/1 failed).

## Deliberate limitations

- **No GUI client verification** — tested via direct HTTP calls (raw browser-binding
  wire format, no mocking), not via a real CMIS desktop/office client (none
  available in this environment) — same limitation already documented for `webdav-connector`.
- **succinct properties only** (5.2.11) — no type-annotated `properties` objects with
  property definitions, see "Only `cmis:name`..." above.
- **No CMIS query** — per ADR 0012, full-text search runs via Postgres FTS
  (`search-service`) anyway, not via CMIS SQL.
- **Contentless checkin not possible** — `document-service`'s versions endpoint requires
  a file for every version.
