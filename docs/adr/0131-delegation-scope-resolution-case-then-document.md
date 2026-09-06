# 0131 — Delegation scope activation: resolve business_key via case-service, fall back to document-service

**Status:** accepted (P32-S2, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 32 Session 2 (post-Phase-31 gap re-analysis), affects `workflow-service`, `user-ui`

## Decision

`workflow-service`'s `_require_delegation_if_on_behalf_of` now resolves `ProcessInstance.business_key`
to an `object_type_id`/`folder_resource_id` before calling `permission-service`'s `GET
/delegations/check` — activating `scope_object_type_ids`/`scope_folder_resource_ids`, which
[ADR 0048](0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md) deliberately left
dead pending "an additional resolution step." The resolution (`_resolve_business_key_scope`) tries
`case-service`'s `GET /cases/{business_key}` first (the real, exercised path — every circulation-folder
process sets `business_key=case_id`), then falls back to `document-service`'s `GET
/documents/{business_key}` (no real process sets a document business_key today, but the field's own
docstring already names this as a "future" possibility, and this reuses an existing endpoint rather than
adding new API surface). `user-ui`'s `DelegationsPane` gains a scope picker for both dimensions.

## Rationale

- **`business_key` is genuinely opaque, but in practice today is always either a case ID or unset** —
  confirmed by finding every real `start_instance` caller in the codebase (only `case-service`, always
  `business_key=case_id`, and `migration-service`, which never sets one). No document-keyed process
  exists yet, despite `ProcessInstance.business_key`'s own docstring aspirationally naming "a future
  `document_id`." Given this codebase's own precedent of trying multiple resolution paths for a
  genuinely ambiguous reference, and the marginal cost being one extra HTTP call against an
  already-existing endpoint (not new API surface), both paths are implemented now rather than only the
  one currently exercised — user's explicit choice when presented with the narrower alternative
  (case-only resolution, with `scope_folder_resource_ids` honestly documented as still-unresolvable).
- **`case-service` has no folder concept, so only `document-service` can ever supply a
  `folder_resource_id`**: confirmed via `CaseOut`'s schema (only `object_type_id`) and case-service's own
  RBAC model (checks only the global `root` resource, never registers per-case `ResourceNode`s).
  `document-service`'s `folder_id` doubles directly as a permission-service `resource_id` with no
  translation (folder-service's `folder.resource.created` event stores `resource_id` verbatim) — this is
  the same identifier space `_delegation_scope_matches` already expects.
- **`GET /documents/{id}` needed no auth header, `GET /cases/{id}` did**: `document-service`'s read
  endpoint has no RBAC gate at all; `case-service`'s does (`case.read`, granted to "everyone" by
  default per ADR 0070). `_resolve_business_key_scope` passes the on-behalf-of deputy's own
  `X-DMS-Principal` for the case lookup — the deputy is the actual acting party in this flow, and
  "everyone" already grants read broadly, so no new service-account identity was needed.
- **A 403/404 case-service response and a 404 document-service response are both treated as "does not
  resolve here," not as an error** — a `business_key` that matches neither service (e.g.
  `migration-service`'s unset case, or some future third process type) must not crash the on-behalf-of
  check; it falls through to `(None, None)`, which the existing `_delegation_scope_matches` fail-closed
  semantics already handle correctly (a scoped delegation is denied, an unscoped one is unaffected).
- **Real end-to-end verification found a genuine test-design conflict, resolved by boundary-patching the
  case-service call in workflow-service's own test suite**: a real `POST /cases` call unavoidably
  triggers case-service's own `workflow_client.start_instance` against the *live* `workflow-service`
  container — but this test suite's own NATS durable-consumer isolation (`scripts/run-tests.sh`)
  requires that exact container to be stopped before running any of `workflow-service`'s own
  `TestClient`-based tests. There is no configuration under which both requirements hold at once, so
  the two `object_type_id` scope tests monkeypatch `app.state.case_client.get_case` (same established
  boundary-patch precedent already used in this file for the event-bus publish call) — `document-service`
  needed no such patch, since document reads don't call back into workflow-service. Both paths were
  additionally verified for real against the live stack outside the test suite (real case via
  case-service, real document via document-service, both scope dimensions confirmed to actually
  allow/deny task completion correctly).
- **UI scope picker covers exactly the two dimensions this session activates, not
  `scope_process_definition_ids`**: that dimension has been live since P17-S3 without any UI; adding it
  now would be a separate, unscoped addition. Object types are already listed elsewhere in `user-ui`
  (reused `listObjectTypes`, a multi-select); no folder picker exists anywhere in the app, so folder
  resource IDs use a comma-separated text field — the same "restriction list as plain text" idiom
  already established for `admin-ui`'s role-permissions field, rather than building a new folder browser
  for this alone.

## Consequences

- **`scope_object_type_ids` is genuinely, verifiably active today** for the one real process type that
  sets a business key (circulation folders/cases) — confirmed live, not just in a mocked test.
- **`scope_folder_resource_ids` is correctly wired but has no real caller yet**: no process type in this
  codebase sets a document business key, so this dimension remains dormant in practice until one does —
  a real, current limitation of the codebase's process types, not a resolution bug. Verified live via a
  document-keyed instance created directly through the API, confirming the code path itself works.
- **`workflow-service` gains two new cross-service dependencies** (`case_service_base_url`,
  `document_service_base_url`, new `case_client.py`/`document_client.py`) with no `depends_on` entry in
  `docker-compose.yml` for either — `case-service` already depends on `workflow-service`, so a mutual
  `depends_on` would be a cycle; both calls are on-demand (task completion only), not needed at startup.
- **No new capability for `scope_process_definition_ids` in the UI** — still only settable via a raw API
  call, same as before this session; a future session could add it using the same picker pattern.
- **Tests**: `workflow-service` +6 (`test_complete_task_on_behalf_of_respects_object_type_scope`/
  `_allows_matching_object_type_scope` via the `case_client` boundary patch;
  `_respects_folder_resource_scope`/`_allows_matching_folder_resource_scope` via a real document — no
  patch needed). `user-ui` +1 (`DelegationsPane` scope-picker submission). `docs/services/
  workflow-service.md`/`permission-service.md`/`user-ui.md` updated.
