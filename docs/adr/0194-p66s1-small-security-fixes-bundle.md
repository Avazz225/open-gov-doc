# 0194 — Small security fixes bundle: webdav MOVE scope, storage-service maintenance endpoints, audit-trace role existence check

**Status:** accepted
**Context:** P66-S1 (Phase 66, "Security/Correctness Quick Wins" — first session of the eighth
gap-analysis round's plan). Three unrelated, small fixes, bundled by size (each is cheap) not theme.

## Decision

**(a) webdav-connector: `handle_move` now rejects MOVE outright for any edit-token-scoped session.**
ADR 0189/P61-S4 introduced `environ["dms.webdav_edit_token_document_id"]` and checked it inside
`get_resource_inst()`, closing arbitrary cross-document read/write for a token-scoped session — but that
ADR explicitly named its own accepted residual: `handle_move()`'s destination-path resolution
(`resolve_path`) still ran against the real underlying user's own, unrestricted folder permissions,
letting a token-scoped session move its one authorized document into any folder that user can write to.
`handle_move()` now checks the same marker before doing anything else and raises `DAVError(HTTP_FORBIDDEN,
...)` unconditionally when it is set — a token-scoped session has no legitimate reason to move anything
(Office's own check-in flow only ever issues `PUT`), so the fix is an outright rejection, not a narrower
re-scoped check.

**(b) storage-service: the three previously-ungated aggregate/maintenance endpoints now require a
trusted caller.** ADR 0179/P59-S2 gated the eleven object-CRUD endpoints behind
`_require_storage_caller`/`_TRUSTED_STORAGE_CALLERS`, but `GET /storage/usage`,
`POST /replication/process-pending`, and `POST /object-verify/process-pending` were left completely
unauthenticated. All three now carry the same `Depends(_require_storage_caller)` gate. Two new entries
were added to the trusted-caller allowlist to support this: `"reporting-service"` (needs
`GET /storage/usage` for its own usage reports) and `"system:storage-replication-cronjob"` (the identity
a scheduled job would use to trigger the two maintenance endpoints).

Fixing (b) surfaced an incidental, independent, already-broken bug: `reporting-service`'s `StorageClient`
never sent an `X-DMS-Principal` header at all, on any of its three methods (`get_usage`/`upload`/
`download`) — meaning every one of its calls to storage-service's object-CRUD endpoints (gated since ADR
0179) would already have been silently rejected with `403` in production, independent of this session's
new gating. Fixed alongside, in the same session, per this project's established "incidental fix,
discovered while touching adjacent code, documented transparently" pattern.

**(c) document-service: `PUT /audit-trace-role-overrides/{role}` now validates the role exists.**
Previously accepted any free-text role name with no existence check — the service's own docs already
named this as "the same existing gap as for every other role name in the system". A new
`AuthServiceClient.realm_role_exists()` calls auth-service's ungated `GET /realm-roles` and the endpoint
now returns `422` for an unknown role before upserting the override.

## Rationale

- **Why an outright MOVE rejection for (a), not a second, narrower destination-scope check**: ADR 0189
  itself already weighed and rejected building "a second, separately-scoped check with no established
  precedent elsewhere in this file" for this exact residual, on the grounds that the golden path (Office
  editing) never issues MOVE. That reasoning still holds; a hard rejection is strictly simpler than
  designing a second scope check for a code path with no legitimate use.
- **Why extend `_TRUSTED_STORAGE_CALLERS` rather than gate these three endpoints differently from the
  object-CRUD ones**: same trust boundary (any caller identified by a literal `X-DMS-Principal` string
  from the fixed allowlist), same mechanism, no reason to invent a second gate family for a service that
  already has one.
- **Why fix `reporting-service`'s missing header in this session instead of filing it separately**: it
  was discovered directly while implementing (b) — reporting-service would have started receiving `403`s
  from the newly-gated `GET /storage/usage` the moment this session shipped, which would have converted a
  previously-latent bug into an immediately-visible regression. Fixing it in the same session was the
  only way to ship (b) without breaking reporting-service.
- **Why `GET /realm-roles` (already ungated) is an acceptable dependency for (c)**: it returns only role
  names, no sensitive data, and is the same endpoint auth-service already exposes ungated for this exact
  purpose elsewhere in the codebase — no new trust boundary introduced.

## Consequences

- `services/webdav-connector/src/webdav_connector/dav_provider.py`: `handle_move()` now rejects MOVE for
  any `dms.webdav_edit_token_document_id`-scoped session before resolving the destination path.
- `services/storage-service/src/storage_service/main.py`: `_TRUSTED_STORAGE_CALLERS` extended with
  `"reporting-service"` and `"system:storage-replication-cronjob"`; `GET /storage/usage`,
  `POST /replication/process-pending`, `POST /object-verify/process-pending` now depend on
  `_require_storage_caller`.
- `services/reporting-service/src/reporting_service/clients.py`: `StorageClient` now sends
  `X-DMS-Principal: reporting-service` on `get_usage()`/`upload()`/`download()` (previously sent no
  identity header at all — a real, independently-discovered production bug, fixed here).
- `services/document-service/src/document_service/auth_client.py` (new): `AuthServiceClient` with
  `realm_role_exists()`.
- `services/document-service/src/document_service/settings.py`: new `auth_service_base_url`.
- `services/document-service/src/document_service/main.py`: lifespan wires up `AuthServiceClient`;
  `put_audit_trace_role_override` now returns `422` for an unknown role.
- `infra/docker-compose.yml`: document-service gained `DMS_AUTH_SERVICE_BASE_URL`.
- New tests: `webdav-connector` +1 (`test_edit_token_cannot_move_the_scoped_document` — asserts a
  token-scoped MOVE attempt is rejected and the document stays at its original path); `storage-service`
  +4 (each of the three newly-gated endpoints rejects an untrusted caller with `403`, plus one confirming
  the two new trusted callers are accepted); `reporting-service` +1
  (`test_storage_client_sends_a_trusted_principal_header`, a real HTTP round-trip against
  storage-service, not mocked — a mock would not have caught the missing-header bug); `document-service`
  +1 (`test_audit_trace_role_override_with_unknown_role_returns_422`), plus its existing role-override
  tests updated to create a real realm role first. `webdav-connector` 18/18, `storage-service` 166/166,
  `reporting-service` 79/79, `document-service` 413/413.
- All three touched services rebuilt/redeployed. **Live-verified against the real running stack**:
  `storage-service` — `GET /storage/usage` and `POST /replication/process-pending` both return `403` for
  an untrusted `X-DMS-Principal`, `200` for `reporting-service`. `document-service` — `PUT
  /audit-trace-role-overrides/{role}` returns `422` for a role that doesn't exist as a Keycloak realm
  role (the accept path for a real role is covered by the automated regression suite). `webdav-connector`
  — covered by the passing regression test; a token-scoped MOVE now receives a `403`, confirmed via the
  webdav4 client library's own `ForbiddenOperation` wrapper (which the test's exception-type assertion
  needed correcting for, see below).
- **Test-infrastructure note**: `webdav-connector`'s and `reporting-service`'s relevant test files make
  real HTTP calls against live, separately-deployed Docker containers rather than running in-process —
  both initially failed after the code change purely because the containers hadn't been rebuilt yet, not
  because of a logic bug; confirmed by re-running after `docker compose build`/`up -d` and seeing both go
  green. Separately, the new `webdav-connector` test initially asserted the wrong exception type: the
  `webdav4` client library's `move()` always wraps any `403` response into its own `ForbiddenOperation`
  (a `ClientError`, not `HTTPError`), with a fixed, generic message unrelated to the real cause — the
  actual `403`/status code is on `ForbiddenOperation.__cause__`. Fixed by catching
  `WebdavForbiddenOperation` and asserting on `exc_info.value.__cause__`.
