# webdav-connector

**Responsibility:** First reference connector of the connector architecture (Concept 3.3, P12-S1) — makes `folder-service`/`document-service` addressable as a network drive via the WebDAV protocol (RFC 4918) (Windows Explorer/macOS Finder/Word). The DMS is the WebDAV **server** here (not a client of an external repository) — see "Direction decision" below. Second reference connector: [`cmis-connector`](cmis-connector.md) (P12-S4).

**Concept reference:** 3.3, 4.2
**Own Postgres schema:** none (stateless — every request translates live into HTTP calls against `folder-service`/`document-service`, see `libs/dms-connector-sdk`)
**ADR:** [0033 — Server direction, `wsgidav`+`WsgiToAsgi`, synchronous connector SDK](../adr/0033-webdav-connector-server-direction-and-wsgidav.md), [0189 — Edit-token session scope](../adr/0189-webdav-edit-token-scope-and-mail-connector-size-limit.md), [0194 — MOVE now rejected for edit-token sessions](../adr/0194-p66s1-small-security-fixes-bundle.md)

## Direction decision (server vs. client)

Concept 3.3/3.7/3.8/9/12 speak in several places generically of "integration of external repositories" — on its own, ambiguous as to whether a connector opens the DMS *for* external programs or *against* an external repository. Two concrete passages clarify this unambiguously in favor of **server**:

- **Concept 4.2**: "If a user opens a document from within an application (e.g. Word via WebDAV/CMIS integration) for editing, an editing lock is automatically set."
- **ADR 0002** (document locking): "Corresponds to the established optimistic-concurrency/ETag pattern from WebDAV/CMIS, through which the document is addressed by external applications anyway."

Both describe an external program that *addresses* the DMS via WebDAV — not the DMS querying a foreign WebDAV server. `webdav-connector` accordingly implements a WebDAV server.

## Architecture

| Building block | Choice | Rationale |
|---|---|---|
| Protocol engine | [`wsgidav`](https://github.com/mar10/wsgidav) (MIT, actively maintained) instead of a custom implementation | Building WebDAV including Windows Explorer/Finder compatibility yourself (Depth header, If header, lock token semantics, many client-specific quirks) is its own error-prone project. `wsgidav` has a documented extension point for non-filesystem backends (`DAVProvider`/`DAVCollection`/`DAVNonCollection`). |
| WSGI in an otherwise consistently ASGI/FastAPI project | `asgiref.wsgi.WsgiToAsgi` mounts the wsgidav app under `/webdav` **inside** a normal FastAPI service (`app.mount()`) | The service stays externally consistent with all other services (`/healthz`, `BaseServiceSettings`, registry self-registration, license check run as normal FastAPI routes) — only `/webdav/*` runs through the bridged wsgidav engine. |
| DMS tree translation | Own lib `libs/dms-connector-sdk` (`DmsTreeClient`), not code in the connector itself | Concept 3.3 requires a reusable SDK — the future CMIS connector (P12-S4) needs the same DMS-side logic, just a different protocol layer on top. |
| WebDAV LOCK/UNLOCK | Directly against `document-service`'s existing lock endpoints (`POST`/`DELETE`/`GET /documents/{id}/lock`, 4.2) | No second, competing lock system — a file locked via WebDAV is the same server-side lock that the user UI also sees. |

**`DmsTreeClient` is deliberately synchronous** (`httpx.Client`, not `AsyncClient`): wsgidav's `DAVProvider` interface is itself synchronous (WSGI). See `libs/dms-connector-sdk/README.md` for the rationale.

## `mount_path` (an important wsgidav pitfall)

`WsgiToAsgi` mounts the wsgidav app under `settings.webdav_mount_path` (default `/webdav`) — wsgidav itself knows nothing about this and, without the `mount_path` configuration key, would deliver hrefs relative to its **own** root (`/...`) instead of the actually public URL (`/webdav/...`). WebDAV clients that determine the resource name by stripping the mount prefix from the href (e.g. `webdav4`) then receive mangled names (e.g. `Ordner-abc123` → `bc123`, depending on prefix length) instead of an error — a real, hard-to-diagnose bug encountered during implementation, since `wsgidav` itself responds validly and the mangling only occurs on the client side. Fix: `"mount_path": settings.webdav_mount_path` in the `WsgiDAVApp` configuration dict (`main.py`).

## File metadata comes from the version table, not from `DocumentOut`

`document-service`'s `DocumentOut` (response of `GET/POST/PATCH /documents...`) carries no file metadata (size/content type/checksum) — that lives exclusively on `DocumentVersionOut` of the respective current version (`GET /documents/{id}/versions/{version_number}`). `DmsTreeClient` therefore fetches this with an additional call on every `TreeDocument` construction (create, check-in, move, list) (`_fetch_current_version`) — an extra HTTP round trip per document in a directory listing, deliberately accepted for a reference implementation: WebDAV clients (Explorer/Finder) rely on correct `Content-Length`/`ETag` values, and an incorrect default value (`0`/empty string) would be the worse alternative — an empty string as an ETag even makes wsgidav's own validation (`checked_etag`) fail with `500` (only `None` or a non-empty string are valid), also encountered in practice and the reason `checksum_sha256` is modeled as `str | None` instead of with an empty-string default.

## Writing: the buffer is captured on close, not read afterward

wsgidav's real `do_PUT` handler (`request_server.py`) calls `fileobj.close()` on the buffer returned by `begin_write()` **before** it calls `end_write()`. A `BytesIO.getvalue()` called only in `end_write()` would throw `ValueError: I/O operation on closed file` on an already-closed buffer — encountered in practice, because a direct Python reproduction (without the real HTTP/WSGI path) never called `close()` and therefore didn't show the bug. `DmsDavDocument` therefore uses `_CapturingBuffer`, a `BytesIO` subclass that captures the content on `close()` instead of reading it afterward from the (by then closed) buffer.

## Authentication

`DmsAuthDomainController` (`wsgidav.dc.base_dc.BaseDomainController`) maps WebDAV Basic Auth (what Explorer/Finder/Word send when connecting to a network drive) onto `auth-service`'s existing `POST /login` — no second, connector-own user store. Deliberately not anonymously reachable: unlike the other backend services (whose ports, per ADR 0005, are directly exposed only for developer convenience, with real usage going through the authenticating gateway), a WebDAV connector is its own endpoint, addressed directly by external programs — without real authentication here, every document would be readable/writable by anyone with network access. Digest auth is disabled (Basic over TLS termination in the target environment is considered sufficient).

**Authorization now actually reaches folder-service/document-service too (Post-Roadmap Phase 38 Session 4, [ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md))**: `dav_provider.py`'s `_actor(environ)` (the already-authenticated WebDAV username, previously only used for `created_by`/`deleted_by`/`locked_by` business fields) is now also forwarded as `x_dms_principal` on every `DmsTreeClient` call. This is what actually makes teamspace membership matter through WebDAV: a non-member genuinely cannot browse/read/write a teamspace's folder via WebDAV, and a member can, exactly like direct API access. A fixed service identity (the pattern used by purely internal/background callers elsewhere in this session) would not have worked here — it isn't a teamspace member and would incorrectly block legitimate access for real users.

## Office direct editing: `by-id` path + edit token (ad hoc post-roadmap, see ADR 0061)

Two additive extensions, without changing the existing path-based flow:

- **`DmsDavProvider.get_resource_inst()`** recognizes paths with the prefix `by-id/` (e.g. `by-id/<document-id>.docx`) BEFORE the usual `resolve_path()` tree traversal and resolves them directly via `self.tree.get_document(document_id)` — O(1) instead of O(tree depth). The `.ext` suffix is purely cosmetic (Office's file type detection on opening) and is discarded server-side.
- **`DmsAuthDomainController.basic_auth_user()`** treats an empty password as a sign that the provided username is a `document-service` `WebdavEditToken`, not a real username: resolves it against `GET /internal/webdav-edit-tokens/{token}` (east-west, directly against `document_service_base_url`, no detour via `/login`) and overwrites `environ["wsgidav.auth.user_name"]` with the resolved `principal_id` — not leaving the raw token in place, otherwise a later check-in would incorrectly use the token instead of the real identity as the lock holder. The existing username+password branch (real network drive mount) remains unchanged.

Together these two produce the target address for the Office URI handler (`user-ui`): `https://<token>:@<host>/webdav/by-id/<document-id>.<ext>`.

**Edit-token sessions are scoped to a single document (since P61-S4, [ADR 0189](../adr/0189-webdav-edit-token-scope-and-mail-connector-size-limit.md))**: `basic_auth_user()` resolving the token to the real underlying user's identity previously made the rest of that session indistinguishable from that user's real password login — browsable/writable/deletable for anything that principal has permission on via any WebDAV client, not just the one document the token was minted for. `basic_auth_user()` now also stores the token's `document_id` on `environ["dms.webdav_edit_token_document_id"]`; `get_resource_inst()` rejects (`403`) any request whose path does not resolve to exactly `by-id/<that document_id>` for such a session (the bare `by-id/` namespace path itself stays reachable — it reveals nothing, `_ByIdVirtualCollection.get_member_names()` always returns an empty list). **Closed (P66-S1, [ADR 0194](../adr/0194-p66s1-small-security-fixes-bundle.md))**: the previously-accepted residual — a `MOVE` request for the one scoped document could relocate it into any folder the real underlying user has write access to, since destination-path resolution used the real identity's own permissions, not a second scope check — is now closed outright: `handle_move()` rejects `MOVE` with `403` unconditionally for any edit-token-scoped session before resolving the destination at all (a token-scoped session has no legitimate reason to move anything; Office's own check-in flow only ever issues `PUT`).

## Licensing (3.3, P9-S2 pattern)

Concept 3.3 explicitly names connectors as an example of licensable components. `registry-service`'s `licensable_components` contains `"webdav-connector": "demo"` (identical pattern to `workflow-service`, see `docs/services/registry-service.md`). Since the actual WebDAV traffic does not run through FastAPI routes (no `Depends()` gate possible), `DmsDavProvider.check_license(action)` checks directly in the wsgidav callback methods: `"unlicensed"` blocks every access (`get_resource_inst`), `"demo"` blocks only write operations (`create_collection`, `end_write`, `handle_delete`, `handle_move` — each for folders and documents).

## Folder lock mapping

A session locked via WebDAV has no native session concept like a browser login (Basic Auth is renewed per request). The `session_id` for `document-service`'s lock endpoints is therefore stable per username (`webdav:<username>`), not per TCP connection — sufficient, since `document-service` tracks locks per document anyway. **Deliberate limitation**: a lock held via a real WebDAV LOCK is not mirrored for the entire editing duration, only during each individual write operation (`end_write()` acquires the lock, holds it for the duration of the upload, releases it again in `finally`) — a Word document opened via WebDAV therefore does not hold document-service's lock continuously between opening and saving, only during the actual save operation.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | `document-service` address |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | `folder-service` address |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | For `DmsAuthDomainController`'s `POST /login` check |
| `DMS_WEBDAV_ROOT_FOLDER_ID` | `root` | DMS folder that appears as the WebDAV root |
| `DMS_WEBDAV_MOUNT_PATH` | `/webdav` | Mount prefix, see "wsgidav pitfall" above |
| `WEBDAV_CONNECTOR_PORT` | `8027` | Host port in the dev compose stack |

Mount in the dev stack e.g. via `net use`/"Map network drive" to `http://localhost:8027/webdav/`.

## Deliberate limitation: no GUI client verification

This session was tested via a real WebDAV **client** (`webdav4`, MIT, test dependency only) against the running instance — PROPFIND/GET/PUT/MKCOL/MOVE/LOCK/DELETE, no protocol mocking. Real Windows Explorer/macOS Finder compatibility could not be tested in this environment (no GUI client available) — `wsgidav` is the engine used and tested for this in practice; a human should mount it for real once before production use.

## Open Points

- ~~No own `GET /metrics` (10.1) — as a pure protocol translator without its own business data, no own sensor is currently defined.~~ — **closed** (found stale in Phase 65+'s gap-analysis round): `main.py` now has a full generic HTTP sensor rollout (`SensorConfigClient`, `bootstrap_http_sensors`, `http_sensor_declarations`) and a real `GET /metrics` endpoint.
- Property/attribute access (`object_type` attributes) cannot be represented over WebDAV (RFC 4918 only knows dead properties, no structured custom metadata as the DMS knows it) — attributes remain visible exclusively via the user UI/API, not via the WebDAV connector.
- ~~**Root `PROPFIND` performance degrades with document volume**~~ — **closed in Phase 50 Session 4**: listing a folder used to call `document-service` once PER contained document for version metadata (size/content-type/checksum, which doesn't live on `DocumentOut`) — a genuine N+1 (`DmsTreeClient._to_tree_document_enriched`, confirmed to be an N+1 against a real, folder-scoped call, not an unpaginated global fetch). Fixed via a new batch endpoint, `POST /documents/versions/current/batch` (`document-service`), consumed once per folder listing instead of once per document — `list_children` now makes exactly one call regardless of how many documents the folder contains, live-verified against the real running stack (a real 5-document folder produced exactly one `POST .../batch` call, correct file sizes round-tripped). Since `cmis-connector` shares the same `dms-connector-sdk` client, it gets the same fix automatically — confirmed via its own test suite (`16/17` passing, the one failure a pre-existing, already-documented, unrelated authorization finding from Phase 50 Session 1). This was found live during Post-Roadmap Phase 38 Session 3's full backend-suite run (unrelated RBAC work at the time) — the accumulated clutter that originally surfaced it (2,472 documents, later 236/1039) was traced to `teamspace-service`'s own test suite leaking real folder-service folders + permission-service resource nodes on every run (it deliberately runs against the real, live neighbor services, no mocking — its own DB-level test isolation only ever covered its own `Teamspace` row, never the side effects in those other services). One-time cleanup purged the dev database via each service's own DELETE/purge endpoints; `teamspace-service`'s test suite now has an autouse teardown fixture that purges what it creates, so this specific leak does not recur. See [ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md) for the full story, including why this session's own `inherit=False` mechanism turned a subset of that clutter into permanently-locked dead folders (also fixed).
- **`_dav_client`/raw `httpx` calls in `test_webdav.py` now use `timeout=30.0`, not the 5s default** (Post-Roadmap Phase 38 Session 4) — a legitimate root listing over a busier real installation can reasonably take longer than 5 seconds given the O(N) shape above; matches `DmsTreeClient`'s own internal client timeout.
