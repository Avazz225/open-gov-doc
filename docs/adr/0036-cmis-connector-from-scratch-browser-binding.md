# 0036 — CMIS Connector: hand-implemented Browser Binding instead of a library

**Status:** accepted
**Context:** P12-S4 (Concept 3.3, "Connector Architecture"). Second reference connector after
`webdav-connector` (P12-S1, ADR 0033) — deliberately deferred at P12-S0/P12-S1 and added to the
roadmap as its own session. Unlike WebDAV (`wsgidav`, an actively maintained protocol engine),
the scope question only arose after real research: **does a Python library even exist that a
CMIS server could be built on?**

## Research finding (blocking for any implementation decision)

A research task dedicated specifically to this found: **no maintained Python CMIS *server*
library exists anywhere.**

- **Every Python CMIS package found is a client library**; none implements the server
  side: `cmislib` (last release years ago, unchanged), `cmislib3`/`cmislib-maykin`
  (forks of the same unchanged code), `CMIS.PythonLib` (an unchanged legacy library). The only
  exception with real, current maintenance is `drc-cmis` (Maykin Media, EUPL-1.2) — but that too
  is a pure client adapter, not a server implementation.
- **Apache Chemistry's OpenCMIS**, the only real, complete CMIS server framework, is
  **Java-only** — no Python equivalent has ever been produced.
- The only real CMIS **server** implementations that exist at all (NemakiWare,
  Java/AGPL-3.0; a .NET Core `CmisServer`) are neither Python nor license-compatible with
  this project (AGPL-3.0 would be problematic for an embedded library, .NET is not an option in
  a Python project).

**Consequence**: unlike WebDAV, there is no equivalent to "take a library and just plug in the
backend" — the entire protocol layer (URL patterns, `cmisselector`/`cmisaction` dispatch,
succinct-property serialization, form encoding) had to be rebuilt directly from the OASIS
CMIS 1.1 specification itself (Chapter 5, "Browser Binding").

## Decision

**Browser Binding instead of AtomPub/SOAP** (already recorded as a recommendation at P12-S0, now
implemented): of the three CMIS 1.1 bindings, Browser Binding is by far the simplest (JSON +
HTML form semantics over plain GET/POST, no XML-namespace/AtomPub-feed complexity) — a natural
fit with `FastAPI` already (JSON responses, `Form`/multipart parsing), with no XML library needed
for AtomPub entries.

**Succinct properties only** (5.2.11) instead of the full, type-annotated `properties`
representation — significantly reduces the JSON schema surface to implement, and is itself, per
the specification, the more compact mode real clients prefer.

**Deliberately limited functional scope** (~14 endpoints instead of all ~38 selectors/actions
listed in Chapter 5.4): repository info, children/object/content for reads; createDocument/
createFolder/update/move/delete/deleteTree/setContent/checkOut/cancelCheckOut/checkIn for
writes. Omitted: type-system introspection (`typeChildren`/`typeDescendants`/`typeDefinition` —
this DMS has no CMIS-compatible object-type system, see below), Relationships/Policies/Items/ACL
(none of these CMIS object kinds has a DMS equivalent), the CMIS Query Language (full-text search
runs via Postgres FTS anyway, per ADR 0012), full version history (`document-service`'s
check-in history is already treated by the concept as sufficiently complete, see
`webdav-connector`'s versioning mapping). Comparable in scope to the WebDAV core method set from
P12-S1.

**No separate private-working-copy object on checkOut/checkIn/cancelCheckOut**:
`document-service` has no "working copy" as its own entity — the real document lock (4.2)
fulfills exactly the role required by the CMIS PWC mechanism (no third-party access until
checkin/cancelCheckout). The returned "PWC" object ID is therefore deliberately identical to the
original document ID, instead of inventing a second, artificial ID that would not point to any
real second object.

**`objectId` is read from both the URL query string and the POST form**
(5.3.4 vs. 5.4.4.3.3) — both addressing routes are valid per the specification, depending on the
action (form control for most write actions, URL addressing for `createDocument`/
`createFolder`, which have no `objectId` form control).

**`asyncio.to_thread()` for the synchronous `DmsTreeClient` call in write endpoints**: read
endpoints are normal (non-`async`) FastAPI routes (automatically run in the thread pool, as
already with `webdav-connector`), but write endpoints must be `async def` (`await request.form()`
is Starlette-`async`-only) — the actual synchronous SDK call therefore runs via
`asyncio.to_thread()` instead of directly on the event-loop thread, exactly the approach recorded
in ADR 0034 as a future precedent for "synchronous `dms-connector-sdk` calls from `async def`
endpoints."

**Two small, backward-compatible extensions to `libs/dms-connector-sdk`** (shared with
`webdav-connector`): `TreeFolder`/`TreeDocument` got `created_by`/`created_at` (long present in
the underlying `FolderOut`/`DocumentOut` responses, but never previously carried into the
dataclasses — basis for `cmis:createdBy`/`cmis:creationDate`; for `TreeFolder` deliberately
`str | None`/`datetime | None` instead of mandatory fields, see "Consequences"); `write_
document()` got an optional `comment` argument (basis for `cmis:checkinComment`).

## Rationale

- **Browser Binding instead of the other two bindings**: AtomPub/SOAP would have needed an XML
  library plus considerably more boilerplate (namespace handling, feed/entry structures), with no
  added value for this reference implementation — Browser Binding is the variant the
  specification itself describes as the "simplest, optimized for modern web stacks."
- **No CMIS type system for custom object types**: `object-type-service`'s object types (2.2)
  have their own, already complete attribute/constraint model — mirroring an additional,
  parallel CMIS type system (with its own property definitions per attribute type) would be a
  standalone, large feature with no clear mandate in the concept (3.3 requires "integration," not
  "full bidirectional type-system synchronization").
- **PWC ID = original ID instead of an invented second ID**: an invented second ID would need to
  be able to point to a real second object (e.g. for a subsequent `getObject` call on the PWC) —
  without a real second object, that would just be a fig leaf that would immediately surface as
  `objectNotFound` on any follow-up call.

## Consequences

- **A real bug found during live verification (fixed before test completion)**: the
  write routes initially read `objectId` only from the form, never from the query string —
  `createDocument`/`createFolder` therefore always landed in the root folder, regardless of the
  target folder addressed via the URL (surfaced for real by two failing tests: a supposedly
  non-empty folder could still be deleted; a document cascade-deleted via `deleteTree` remained
  unchanged). Fix: both addressing routes are read, with the form control taking precedence.
- **A real bug found during live verification in `folder-service`'s domain (compensated in the
  connector, not fixed in `folder-service` itself)**: `DELETE /folders/{id}` only checks its own
  subfolders for emptiness, never documents (which live in a different service/schema) —
  previously never visible, because the only prior caller (`webdav-connector`) always recursively
  deletes all children first. CMIS' non-cascading `delete` action, however, MUST fail with
  `constraint` (409) for a folder containing documents — `cmis-connector` checks this itself
  (`_tree.list_children()`) before ever calling `delete_folder()`.
- **`TreeFolder.created_by`/`created_at` deliberately optional (`| None`), not mandatory**:
  `resolve_path("")` (empty path, root addressing) still constructs the root purely locally,
  without an HTTP call — this branch runs on **every** WebDAV request to the root
  (`get_resource_inst("/")`), where an additional round trip would be a noticeable performance
  regression. An initial attempt to actually call `get_folder()` instead was reverted again
  during live verification (see next point) — `created_by`/`created_at` remain `None` in this one
  case (CMIS' own "value not set" state, 5.2.7) rather than invented values.
- **A real performance finding during live verification** (not a code bug): `webdav-connector`'s
  test suite failed with `httpx.ReadTimeout` on PROPFIND against the WebDAV root after the
  `--build` full regression run. The cause was **not** the experimentally introduced
  `get_folder()` variant (the timeout persisted unchanged after reverting it) and **not** a
  deadlock, but test data accumulated over this conversation's very long project runtime in the
  shared dev root folder: 68 subfolders + 74 documents directly under `root` (among others from
  `webdav-connector`'s, `cmis-connector`'s, and `migration-service`'s own respective test runs,
  none of which clean up the objects they create at `root`). `DmsTreeClient.
  list_children()` deliberately fetches one additional HTTP call per document (see its
  docstring) — with 74 documents at the root alone, a single root PROPFIND request thereby took
  over 10 seconds (verified: `curl --max-time 60` answered it in 10.5s), long enough to exceed
  typical test-client timeouts. Fixed by a one-time cleanup
  (`POST /folders/{id}/trash` resp. `DELETE /documents/{id}` for all 142 root objects, all
  identifiable by test-actor names like `webdav-test-*`/`cmis-test-*`/`connector-sdk-tests`/
  `migration-tests`) — reduced the same request to 76ms. No code change needed, but documented
  evidence that `list_children()`'s "deliberately accepted" O(documents) overhead (see the SDK
  docstring) becomes noticeable in practice with a root left uncleaned across many sessions.
- **Deliberate limit: no GUI client verification** — tested via direct HTTP calls in the raw
  Browser Binding wire format (no mocking), not via a real CMIS desktop/office client (none
  available in this environment) — the same limitation already documented for `webdav-connector`.
- **Deliberate limit: content-less checkin not possible** — `document-service`'s version
  endpoint requires a file for every version; a pure metadata/comment checkin with no content
  change is rejected with `invalidArgument` (400) instead of an artificial "empty" upload.
- **Precedent**: a future third connector that likewise finds no library equivalent should follow
  the same path — read the specification directly (do not guess), deliberately limit scope to
  the DMS concepts that actually exist, honestly document gaps as a "deliberate limit" instead of
  concealing them or building imaginary behavior.
