# 0151 — Role update gets four-eyes; `access_type` validated against `permission`'s naming convention

**Status:** accepted
**Context:** P39-S2 (Post-Roadmap Phase 39), first two of three named "remaining four-eyes/validation
gaps" (see `IMPLEMENTATION_PLAN.md` "Phase 39"). [ADR 0130](0130-role-and-role-assignment-four-eyes.md)
(P32-S1) added the generic four-eyes mechanism ([ADR 0022](0022-four-eyes-approval-via-events.md)) to
`POST /roles` and `POST /role-assignments`, explicitly leaving `PUT /roles/{id}` (role update) out —
"no direct precedent to mirror" at the time. That precedent now exists (its own sibling endpoint), so
this session closes the gap. Separately, `GET /check`/`POST /check/batch` take `permission` and
`access_type` as two independent parameters with no relationship enforced between them — `access_type`
alone drives scope-lock blocking severity (`blocking_locks = [... if access_type == "write" or
lock.blocks_read]`), so a caller passing a `permission` ending in `.write` together with
`access_type="read"` gets read-severity scope-lock treatment for what is actually a write. The third
named gap (maintenance mode vs. direct service-to-service writes) is deliberately **not** part of this
ADR — see [ADR 0152](0152-maintenance-mode-service-to-service-enforcement-scoping.md), a dedicated
scoping-only session per explicit user choice.

## Decision

**`PUT /roles/{role_id}` now returns `RoleActionResult` (same envelope as `POST /roles`) instead of a
bare `RoleOut`, and is four-eyes-gated behind `permission.role.update`.** After the existing
`_require_role_management` baseline check, the endpoint reads
`repository.get_approval_config(session, "permission.role.update")`; if `requires_approval` is set, it
calls the existing `_request_approval()` with the update's `role_id`/`description`/`permissions` as
payload and returns `status="pending_approval"` — otherwise it calls `repository.update_role()`
directly and returns `status="updated"`. `RoleActionResult.status`'s `Literal` gains `"updated"`
alongside the pre-existing `"created"`/`"pending_approval"`. No seeding/migration step is needed:
`get_approval_config()` already returns a transient, non-persisted `requires_approval=False` default
for any action type with no persisted row, so `permission.role.update` starts out ungated, exactly like
`permission.role.create` did when ADR 0130 first introduced it — an admin opts in later via the existing
`PUT /approval-config/permission.role.update`.

`approval_consumer.py`'s `make_handler()` gains a matching `permission.role.update` branch (added to
`known_action_types` and as a new `elif` before the pre-existing `permission.role.create` `else`
branch): it calls `repository.update_role()` with the approved payload and publishes
`permission.role.updated`. Unlike `create_role`, `update_role` already has a real not-found
precondition (`repository.NotFoundError` for an unknown `role_id`) — this is caught by the same
outer `except (repository.NotFoundError, KeyError):` every other branch in this consumer already uses,
so a role deleted between request and approval surfaces as a logged warning, not an endless NATS
redelivery loop.

The one known production caller outside `permission-service` itself,
`config-service`'s `PermissionServiceClient.update_role()` (used by `apply_roles()` during
configuration import, 7.3), is unaffected: it already does `response.raise_for_status(); return
response.json()` with no shape-specific field access, and its caller (`apply_roles()`) does not inspect
the returned value at all — the identical, already-accepted characteristic its sibling `create_role`
branch in the same function has had since ADR 0130, despite `create_role` having four-eyes since that
same session.

**`GET /check`/`POST /check/batch` gain a new `_validate_access_type(permission, access_type)` helper**,
called at the start of both endpoints. It derives an *expected* `access_type` purely from `permission`'s
own suffix — `.read` → `"read"`, `.write` → `"write"` — and raises `422` on a mismatch. A permission
with neither suffix (e.g. `admin.user_management`, `reporting.forensic_trace`, or a bare `"read"`/
`"write"` used by older tests) is left unchecked: there is no textual convention to validate it against,
and inventing one (e.g. a hardcoded permission→access_type lookup table) would need ongoing maintenance
as new permissions are added, with no corresponding benefit — the `.read`/`.write` suffix convention
already covers the overwhelming majority of permissions actually checked via `/check`/`/check/batch`
(every resource-scoped permission introduced since [ADR 0149](0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)'s
broad RBAC retrofit follows it: `document.read`/`.write`, `folder.read`/`.write`,
`document.share_link.read`, `document.webdav_edit.write`, `document.redaction.read`,
`document.export.read`, `case.read`/`.write`, `ocr.read`/`.write`, `rendering.read`/`.write`,
`archival.read`/`.write`, `virus_scan.read`/`.write`, `workflow.write`, `notification.write`,
`reporting.read`/`.write`, `audit.read`).

No existing production caller needed a change: every one of them (verified across `archival-service`,
`case-service`, `document-service`, `folder-service`, `notification-service`, `ocr-service`,
`query-service`, `rendering-service`, `reporting-service`, `search-service`, `virus-scan-service`,
`workflow-service`) already derives its `permission` string FROM `access_type` at the call site (e.g.
`folder-service`'s `permission = "folder.read" if access_type == "read" else "folder.write"`), so the
two can never have been out of sync by construction — the new check makes that pre-existing invariant
enforced server-side instead of merely true by convention at every caller today, closing the door on a
*future* caller getting it wrong.

## Rationale

- **Mirroring `create_role`'s exact four-eyes wiring for `update_role` instead of inventing a new
  pattern**: `PUT /roles/{id}` is `POST /roles`'s direct sibling — same baseline check, same
  `get_approval_config`/`_request_approval` call shape, same consumer-side execution-on-approval model.
  A role's `permissions` list is exactly the kind of "permission change" (14.2) four-eyes already exists
  to slow down; ADR 0130 leaving this endpoint out was an explicit gap, not a considered exclusion.
- **Suffix-derived validation instead of a maintained permission→access_type table**: the research for
  this session found zero real callers today where `permission` and `access_type` are chosen
  independently — every one derives `permission` FROM `access_type`. A static table would duplicate
  information that's already encoded in the permission string's own name, and would silently go stale
  the moment a new permission is added without a corresponding table entry (failing open, not closed).
  Deriving the expectation from the name itself needs no maintenance and degrades safely: an
  unconventional permission name is simply left unchecked, never incorrectly rejected.
- **422, not 403**: this is a client request-shape error (self-contradictory parameters), not an
  authorization decision — `permission-service` already uses `422` for other request-validation
  failures via FastAPI/Pydantic's own default behavior for malformed input.

## Consequences

- **`PUT /roles/{id}`'s response shape changes** from a bare `RoleOut` to `RoleActionResult` — a
  breaking change for any caller that inspected the old bare shape (`response.json()["description"]`
  instead of `response.json()["role"]["description"]}`); the only known production caller
  (`config-service`) is unaffected as described above. Any external/undocumented caller not covered by
  this repo's own tests would need to adapt, the same accepted trade-off ADR 0130 already made for
  `POST /roles`.
- **`permission.role.update` starts ungated by default**, same as `permission.role.create` did — an
  operator who wants four-eyes on role updates must explicitly configure it via `PUT
  /approval-config/permission.role.update`, exactly mirroring the existing operational step for role
  creation.
- **`access_type` validation only covers the `.read`/`.write` naming convention**, not every permission
  in the system. A capability-style permission (`admin.*`) or an unconventionally-named one remains
  fully uncheckable by this mechanism; closing that would require either a maintained mapping (rejected
  above) or restructuring `/check`'s parameters so `access_type` is derived server-side instead of
  passed by the caller at all — a larger, not currently justified change, since no real caller today
  passes a permission without the convention through `/check` with a meaningful `access_type` at all.
- **`GET /check`/`POST /check/batch` now reject a previously-silently-accepted mismatched call with
  `422`** instead of applying the wrong scope-lock blocking severity — any caller that was relying on
  the old, unchecked behavior (none found in this codebase) would need to fix its call. Callers that
  pass a bare, non-namespaced permission (`"read"`/`"write"`, still used by a few of this service's own
  older tests) are unaffected, since those have no `.read`/`.write` suffix to validate against.
