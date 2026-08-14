# dms-connector-sdk

Connector architecture (Concept 3.3, P12-S1): reusable DMS-side integration for
connector services (WebDAV, CMIS) — knows only `folder-service`/`document-service`, no protocol
(WebDAV/CMIS remains the concern of the respective connector service itself).

- **`ConnectorCapability`/`ConnectorDescriptor`** (`capability.py`): the "capability description"
  required by 3.3 (`read`/`write`/`metadata`/`locking`/`versioning`) as a dataclass,
  provides `as_capability_list()` for self-registration with `registry-service`
  (`dms-registry-client`'s `capabilities` field is already a free-form `list[str]`, no
  schema change needed, see P12-S0 research).
- **`DmsTreeClient`** (`dms_tree_client.py`): deliberately **synchronous** (`httpx.Client`, not
  `AsyncClient`) — the first connector (`webdav-connector`) is built on `wsgidav`, whose
  `DAVProvider` interface is itself synchronous (WSGI); an async variant would have needed an
  async/sync bridge (`asgiref.async_to_sync`) across several nested thread/loop boundaries -
  a known fragile pattern. The second, FastAPI-based connector (`cmis-connector`,
  P12-S4) uses this lib safely nonetheless: FastAPI automatically runs regular `def` endpoints
  (not `async def`) in its own thread pool; write endpoints that additionally need
  `await request.form()` explicitly offload the synchronous SDK call via
  `asyncio.to_thread()` (see `docs/services/cmis-connector.md`).

  Provides: path resolution (`resolve_path()`, segment-wise from `root`, O(depth) HTTP calls -
  deliberately simple, no cache with invalidation issues), direct access by ID
  (`get_folder()`/`get_document()`, since P12-S2 — for callers that know an ID instead of a path,
  e.g. `migration-service`), folder/document CRUD, PUT semantics (`write_document()`
  creates or checks in a new version, depending on whether `existing_document_id` is set;
  optional `comment` argument since P12-S4, passed through to `document-service`'s
  `POST /documents/{id}/versions` comment field — the basis for CMIS's `checkinComment`),
  moving (`move_document()`/`move_folder()`, uses `document-service`'s new
  `folder_id` field since P12-S1, and `folder-service`'s existing `parent_id`), locking
  (`acquire_lock()`/`release_lock()`/`get_lock()` — thin wrappers around `document-service`'s
  existing lock endpoints, **no lock logic of its own**: a version locked via a connector is
  server-side the same lock the user UI would also see).
- **`TreeFolder`/`TreeDocument`** now also carry `created_by`/`created_at` since P12-S4 (both
  fields had long existed in the underlying `FolderOut`/`DocumentOut` responses, but had never
  been carried over into the dataclasses — the basis for CMIS's
  `cmis:createdBy`/`cmis:creationDate`, still without consequence for `webdav-connector`, since it
  does not read them).

Error cases are deliberately kept small (`PathNotFoundError`, `LockConflictError`,
`LockNotHeldError`) — the actual protocol translation (WebDAV status codes, CMIS error objects)
remains the concern of the respective connector service, not this lib.

See `services/webdav-connector/` for the first reference implementation (P12-S1),
`services/cmis-connector/` for the second (P12-S4, a hand-implemented CMIS 1.1 browser
binding instead of a library — see ADR 0036 for why no maintained Python CMIS server lib
exists), and `services/migration-service/` for a third consumer (P12-S2, reads the
source installation via this lib and uses the same lib again on the target installation to
actually create what it received).
