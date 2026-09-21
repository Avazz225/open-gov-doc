# 0144 — A real per-case RBAC resource type for `case-service`

**Status:** accepted (P35-S2, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 35 Session 2 (org-hierarchy & workflow polish), affects `case-service`/`permission-service`/`libs/dms-permission-client`

## Decision

`case-service` now registers a real `ResourceNode` per case in `permission-service` (`resource_type="case"`,
`parent_id="root"`), analogous to `folder-service`'s own resource-tree registration — closing the gap ADR
0070 and ADR 0121 both explicitly left open ("case-service registers no own nodes... a per-case permission
control would need its own resource hierarchy, analogous to the already-open point for documents"). Every
per-case endpoint (`GET`/`PATCH /cases/{id}`, the document-reference endpoints, archive-request/status)
now checks `case.read`/`case.write` against `resource_id=case_id` instead of the collection-level `"root"`;
collection-level endpoints (`POST`/`GET /cases`, the by-vorgangsnummer lookup, the two config endpoints)
stay at `root` unchanged, since no single case exists yet to check against there.

Unlike `folder-service`'s purely event-driven registration, the node is created **synchronously** — a new
`POST /resources` endpoint on `permission-service` (idempotent create-if-missing, sharing its logic with
the existing `"*.resource.created"` event handler) is called directly from `case-service`'s `create_case`,
so the node is guaranteed to exist by the time the HTTP response returns, not eventually via NATS. The
`case.resource.created` event is still published too, for symmetry with `folder-service`'s contract and as
the basis for a startup backfill loop that registers every case created *before* this session.

## Rationale

- **Why synchronous, not purely event-driven like `folder-service`**: an unregistered `resource_id` denies
  every permission check outright (`_collect_effective_roles` breaks the ancestor walk immediately if the
  starting node doesn't exist — no roles at all, not even a fallback to `root`). `folder-service`'s own CRUD
  endpoints never self-check per-resource (that happens at the gateway, decoupled from the exact moment a
  folder is created), so a propagation window there is invisible to any single request. `case-service`'s
  own endpoints DO self-check per-resource, and a genuinely common flow — create a case, immediately read
  or add a document to it — would otherwise race the NATS consumer, occasionally 403ing a fully authorized
  principal on their own freshly created case. `permission-service` is already a hard, synchronous
  dependency of every case-service request (via the existing `_require_case_permission` write-check at
  `POST /cases` itself), so adding one more synchronous call to the same dependency introduces no new
  failure mode.
- **`POST /resources` is deliberately ungated**, same reasoning as `POST /org-hierarchy-grants` (ADR 0121):
  no internal service-to-service authentication exists anywhere in this project (a documented, accepted
  gap), and the event-driven path it mirrors carries no authentication either — this endpoint is no less
  trusted than the event bus it's a synchronous counterpart to.
- **The event is still published, not dropped, once the synchronous call already guarantees existence** —
  it costs three lines, keeps `case-service` symmetric with `folder-service`'s own structure-event
  contract for any future consumer that might want to observe case creation via the event bus, and powers
  the startup backfill's self-healing (a case whose original registration was missed, e.g.
  `permission-service` was transiently unreachable, is recovered on the next `case-service` restart).
- **Startup backfill, not a one-shot migration endpoint**: every case created before this session has no
  `ResourceNode` at all. Re-running the (idempotent) synchronous registration for every existing case on
  every `case-service` startup is deliberately simple — a no-op once caught up, self-healing for any missed
  registration, and avoids inventing a one-shot admin endpoint or a migration-state column purely to avoid
  re-checking cases that are already registered. Live-verified against the real dev stack's 157
  pre-existing cases (spanning the whole project history back to early P7 sessions) — all were already
  correctly registered by the time of this session's live verification, confirming the backfill loop
  actually ran successfully on a prior container restart during this session's own test cycle.
- **A real, distinct bug found and fixed while building this**: reordering the checks surfaced that
  `GET /cases/does-not-exist` (and the other per-case endpoints) would have started returning `403`
  instead of `404` for a genuinely unknown `case_id` — an otherwise fully authorized principal, denied only
  because the nonexistent case also has no `ResourceNode`. Fixed by checking case existence FIRST (a new
  `_get_case_or_404` helper, called before `_require_case_permission` in all seven per-case endpoints) —
  you cannot meaningfully authorize an action against a resource that doesn't exist, and this project's own
  existing test suite already encoded the expectation that an unknown case returns `404`, not `403`.
- **`RoleAssignment`s scoped to a specific case need no new admin-ui work** — `admin-ui`'s existing
  `UserManagement` role-assignment form already has a generic, free-text `resourceId` field (default
  `root`, already editable); once a case has a real `ResourceNode`, an admin can already scope a role to it
  through the existing UI, no new page or field needed. This session is deliberately backend-only, matching
  the plan's own framing ("a real resource type... so future case features can build on it", not "build a
  case-ACL UI").

## Consequences

- **Tests**: `permission-service` 166 tests (up from 164, +2) — `POST /resources` creates a node
  synchronously with no polling needed, and is idempotent (never overwrites an existing node's
  `parent_id`/`resource_type`). `case-service` 65 tests (up from 61, +4) — a case's `ResourceNode` exists
  the instant `POST /cases` returns (no polling), the `case.resource.created` event is also published
  (symmetry), a case-specific `RoleAssignment` genuinely restricts read access to just that one case (the
  decisive proof this session delivers real value, not just plumbing), and collection-level endpoints still
  check `root` unchanged; one existing test (a case created via direct `repository` access, bypassing
  `POST /cases`'s registration) additionally updated in place to register the `ResourceNode` explicitly, no
  net count change from that one. `libs/dms-permission-client` 14 tests (up from 12, +2) — the new
  `create_resource_node` method posts the expected body and defaults `resource_type` to `"folder"`.
- **Live-verified against the real, rebuilt running stack**: created a real case and confirmed its
  `ResourceNode` and a real `GET /cases/{id}` both succeeded with zero delay (no propagation wait). Stripped
  `case.read` from the "everyone" role globally, confirmed the case (and an unrelated pre-existing one)
  both became correctly unreadable; created a case-scoped role + `RoleAssignment` at the specific case's
  `resource_id`, confirmed the scoped principal could read exactly that one case and no other; restored
  "everyone"'s original permissions and confirmed normal access returned. Confirmed the existence-check-first
  fix live: `GET /cases/does-not-exist` correctly returns `404`. Confirmed the startup backfill against the
  real dev stack's 157 pre-existing cases (spanning back to early Phase 7 sessions) — all found already
  registered.
- ~~**`GET /cases`/`GET /cases/by-vorgangsnummer` still cannot filter per-case** — they remain root-scoped,
  all-or-nothing collection reads; per-row filtering by each case's own resource would need a bulk-authz
  check against every returned row, a genuinely separate, larger feature not part of this session's
  "retrofit the resource type" scope.~~ — **closed by ADR 0154** (P39-S4): `_filter_cases_by_permission()`
  row-level filtering via `check_batch`.
- **No admin-ui changes** — the existing generic role-assignment UI already covers the new capability (see
  Rationale); a dedicated case-ACL management page, if ever wanted, remains explicitly future work. **Still
  genuinely open as of Phase 65+'s gap-analysis round** (low priority — cosmetic/UX completion, not a
  functional gap).
- ~~**`workflow-service`'s org-hierarchy grants (ADR 0121) are NOT migrated to case-scoped `Delegation`s** —
  they remain scoped by `process_definition_id`, unchanged; whether/how to narrow them to the specific case
  instance using this session's new resource type is a separate, explicitly deferred follow-up (ADR 0121's
  own "Consequences" already named this as out of its scope).~~ — **closed by ADR 0154**: new
  `scope_case_resource_ids` delegation dimension.
