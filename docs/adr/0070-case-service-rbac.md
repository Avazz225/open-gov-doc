# 0070 — case-service RBAC

**Status:** accepted (session 5 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 5, affects `case-service`, `permission-service`,
`archival-service`, `mail-connector`, `infra/docker-compose.yml`

## Decision

`case-service` previously had **no** permission checking at all — not even an
`X-DMS-Principal` header check like other services have ahead of their respective enforcement. This
session closes that gap:

1. **New `_require_case_permission(x_dms_principal, *, access_type)` helper** (`main.py`) — `401` without
   `X-DMS-Principal`, otherwise `PermissionServiceClient.check(principal_id=..., resource_id=ROOT,
   permission="case.read"|"case.write", access_type=...)`, `403` on denial. **First consumer of
   `libs/dms-permission-client`** (P19-S1) at all — no more own, duplicated
   `permission_client.py` needed.
2. **All human-usable endpoints gated**: `POST/GET /cases`, `GET /cases/by-vorgangsnummer`,
   `GET /cases/{id}`, `POST/DELETE .../documents`, `GET .../documents`, `POST .../archive-request`
   (a human action despite being "instance-modifying"), `GET .../archive-status`, all four config
   endpoints (`case-archival-config`, `case-number-config`).
3. **Two purely internal machine-to-machine callbacks deliberately left UNGATED**: `GET
   /cases/due-for-archival` and `PUT /cases/{id}/archived` — both are called exclusively by
   `archival-service`, which currently sends no identity header for them at all. Exactly the same,
   already pre-existing gap as `document-service`'s analogous `PUT /documents/{id}/archived` (also
   ungated, confirmed) — a general service-to-service authentication is a larger,
   project-wide decision outside this session.
4. **`resource_id` is always `"root"`** — case-service (like document-service for its
   documents) registers no own nodes in the permission-service resource tree; a circulation folder
   has no parent folder node per concept 2.3 anyway. A fine-grained, circulation-folder-owned
   resource hierarchy is a larger, still-open architecture topic (see "Consequences").
5. **"everyone" group (ADR 0067) extended with `case.read`/`case.write`** — preserves the previous
   de-facto-open behavior (case-service previously checked NOTHING), but makes it admin-editable
   instead of permanently unchangeably open. Same principle as `users.lookup`/`users.directory`
   in P19-S3.

## Rationale

- **Why `case.read`/`case.write` instead of a new domain admin role**: concept 2.3 explicitly
  describes the circulation folder as an *"independent, RBAC- and constraint-capable object"* — a
  normal, RBAC-governed business object like documents/folders, not an admin domain.
  `document.read`/`.write` is the established naming pattern for exactly this case
  (`<domain>.read`/`<domain>.write`, matching `GET /check`'s `access_type` parameter).
- **Why `POST /cases/{id}/archive-request` is gated but `PUT /cases/{id}/archived` is not**:
  the former is, per its own docstring, a *"manual disposal trigger"* — a human action.
  The latter is explicitly an *"internal callback from archival-service"* — no human caller
  exists to check.
- **Why the "everyone" extension instead of a forced role assignment for all users**:
  case-service previously had no check at all — every authenticated user could read/modify any
  circulation folder. A forced, narrower default role would have crippled the system for every
  existing installation on the next restart (no one would have `case.read`/`case.write` until an
  admin manually assigns roles) - "everyone" preserves continuity, exactly the principle already
  established in ADR 0067/0068.

## A real deployment problem found and fixed

`infra/docker-compose.yml`'s `case-service` block had **no** `DMS_PERMISSION_SERVICE_BASE_URL` set
— `Settings.permission_service_base_url` thereby fell back to its local development default
(`http://localhost:8004`), which points nowhere within the container (`localhost` there is
`case-service` itself, not the Docker Compose network address of `permission-service`). Every
gated call thereby failed with `httpx.ConnectError` (not 401/403!) and a `500 Internal Server Error`
— only discovered during live verification against the real stack (unit/integration tests run with
explicitly set `TEST_*_URL` environment variables, which don't reveal this gap). Fixed by
`DMS_PERMISSION_SERVICE_BASE_URL: http://permission-service:8000` plus `permission-service:
condition: service_started` in `depends_on` (consistent with every other permission-service consumer).

## Two more, independent regressions found and fixed in other services

Two existing services call case-service endpoints east-west that were gated by this session,
**without ever sending an `X-DMS-Principal` header** — both would have failed with
`401` in real operation:

- **`archival-service`'s `CaseClient.get_case`/`.list_document_references`/`.get_archival_config`**
  (`clients.py`) — all three previously called gated endpoints without a header.
- **`mail-connector`'s `CaseClient.lookup_by_vorgangsnummer`/`.get`/`.add_document_reference`**
  (`case_client.py`) — the same gap, affects the automatic file-reference-number matching of
  incoming mail (2.5/3.3).

Both are pure machine callers without a human principal (file-reference-number matching is
triggered by incoming email, not by a user action) — fixed via a synthetic
`X-DMS-Principal` header following the project's already established `"system:<Service>"`
pattern (cf. `actor="system:archival-service"` in published events): `"system:archival-service"` and
`"system:mail-connector"` respectively. Since the "everyone" group grants `case.read`/`case.write`
by default, this works without a dedicated technical account or an explicit role assignment.

**Additionally, three direct, headerless case-service calls found in `mail-connector`'s own test
suite** (`tests/conftest.py::real_case_id`, `tests/test_api.py::_get_case`/`_get_case_documents`) —
these helper functions call the real, running case-service container directly via `httpx` (the
same "no mocking of sibling services" test philosophy used throughout the project), but only surfaced on a
second test run **after** rebuilding the case-service image (the first run still hit the old,
ungated container state) - fixed with the same synthetic `X-DMS-Principal` header.

## Consequences

- **Tests**: `case-service` 50 (previously 45, +5: positive/negative tests for `create_case`/`list_cases`
  `401`/`403`, `archive-request` auth requirement). New fixtures `case_headers`/`everyone_role_without`
  in `conftest.py` (the latter duplicated from `auth-service`'s pattern, project convention).
  `archival-service` 59, `mail-connector` 30 — both unchanged and green after the header fix (their
  tests use fake clients for the affected paths, so they don't cover the real HTTP regression -
  only live verification against the real stack surfaced it). `ruff check`/`ruff format --check`
  clean for all four services.
- **Fully verified live against the real running stack** (after image rebuild of all three
  services + docker-compose fix + restart): `GET /api/case-service/cases` through the gateway → `200`
  (with token) / `401` (without); `GET /case-archival-config` directly with `X-DMS-Principal:
  system:archival-service` → `200` with real configuration data; `GET /cases/due-for-archival` still
  reachable ungated; `mail-connector`'s `GET /cases/by-vorgangsnummer` with `system:mail-connector` →
  `200`.
- **No resource-tree entry for circulation folders** (still open, see
  `docs/services/case-service.md` "Open Points"): `resource_id="root"` is a deliberately coarse,
  provisional compromise - a fine-grained permission control per circulation folder (e.g. only the
  responsible department may read/write) would need its own resource hierarchy, analogous to
  the already-open point for documents. Not part of this session.
- **`document-service`'s own, analogous `PUT /documents/{id}/archived` remains ungated** —
  the same gap that already existed before this session, not caused or fixed by this session
  (different service, outside this session's scope).
