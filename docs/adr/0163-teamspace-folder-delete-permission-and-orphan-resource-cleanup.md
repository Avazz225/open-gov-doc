# 0163 — `folder.delete` as a dedicated permission; closing `folder-service`'s orphaned-`ResourceNode` gap

**Status:** accepted
**Context:** P44-S2 (Phase 44, "Security & Correctness Hardening"). Two independent gaps in the same
area (`folder-service`'s deletion paths), bundled into one session per the plan: (a) `teamspace-member`'s
broad `folder.write` let ANY teamspace member - not just a manager - delete or trash the entire teamspace
by calling `folder-service` directly, bypassing `teamspace-service`'s own manager-only guard on
`DELETE /teamspaces/{id}` (which only ever protected the teamspace's metadata row, never the underlying
folder); (b) `folder-service`'s forced-deletion, trash-expiry-purge, manual-purge, and
restore-reconciliation paths never published `folder.resource.deleted`, leaving orphaned `ResourceNode`
rows in `permission-service` on every one of them - an already-identified, then-untracked gap first
named in [ADR 0154](0154-document-per-document-resource-case-list-filtering-org-hierarchy-case-scope.md).

## Decision

### (a) A new, dedicated `folder.delete` permission - not a `folder-service`→`teamspace-service` lookup

`folder-service`'s `DELETE /folders/{id}` and `POST /folders/{id}/trash` now check `folder.delete`
instead of the broader `folder.write` (`_require_folder_delete_permission`, mirroring
`_require_folder_document_reference_permission`'s existing "deliberately its own permission pair"
shape). `folder.delete` is added to `EVERYONE_ROLE_PERMISSIONS` in `permission-service`, preserving
today's default-open delete/trash behavior for every ordinary, non-teamspace folder - this is a
narrowing for teamspaces specifically (via their root folder's pre-existing `inherit=False` isolation,
ADR 0149), not a new restriction project-wide.

`teamspace-service` gains a SECOND role, `teamspace-manager` (permissions: `["folder.delete"]`), granted
IN ADDITION to the existing `teamspace-member` role for members with `can_manage_members=True` only:

- `create_teamspace`: the creator (always the first member, `can_manage_members=true`) gets both roles.
- `invite_member`: `teamspace-member` always; `teamspace-manager` additionally if
  `payload.can_manage_members` is set at invite time.
- `update_member` (`PUT .../members/{id}`): now touches `permission-service` for the first time - grants
  `teamspace-manager` when `can_manage_members` becomes `true`, revokes it when it becomes `false`. Both
  operations are idempotent (see "Consequences"), so no need to diff against the previous value first.
- `remove_member`/`delete_teamspace`: revoke both roles unconditionally for every departing
  member/teamspace - safe even for a member who never held `teamspace-manager` (idempotent no-op).

Existing `folder-service` endpoints deliberately left on `folder.write`: `restore_folder` (undoing a
trash is not itself destructive) and `PATCH /folders/{id}` (rename/move). `POST /folders/{id}/purge`
already used the much stronger, admin-only `admin.deletion` capability (ADR 0150) and needed no change.

### (b) Every real hard-delete call site now publishes `folder.resource.deleted`

Four call sites in `folder-service/main.py` previously called `retention_actions.execute_forced_
deletion`/`purge_expired_trash_entry` and published only a BUSINESS event (`folder.force_deleted`/
`folder.trash_purged`) - never the structure-tree LIFECYCLE event `permission-service`'s
`structure_consumer.py` listens for to delete the corresponding `ResourceNode`. Only the immediate,
synchronous `DELETE /folders/{id}` path ever published it. All four now also publish `folder.resource.
deleted` right after their existing business-event publish: `_execute_or_defer_forced_deletion` (the
retention-poll-driven forced deletion), the retention poll loop's trash-expiry purge, the manual
`POST /folders/{id}/purge` admin endpoint, and `POST /folders/{id}/reconcile-restore-deletion`.

## Rationale

- **Why a new permission, not a `folder-service`→`teamspace-service` lookup**: `folder-service` has no
  concept of "teamspace" today and none is added here - asking it to query `teamspace-service` for "is
  this folder someone's root, and is the caller a manager" would invert the existing dependency direction
  (`teamspace-service` depends on `folder-service`, never the reverse) for a single endpoint's benefit.
  A dedicated permission keeps the fix entirely inside the existing RBAC primitive (`permission-service`'s
  resource tree + isolated-root mechanism, ADR 0149), which was already designed to carry exactly this
  kind of distinction and needed no new capability of its own.
- **Why `folder.delete` goes to "everyone" by default**: the alternative (leaving it off "everyone")
  would 403 every ordinary, non-teamspace folder delete/trash for everyone except whoever holds it
  explicitly - the same "system-breaking regression, not a security fix" trade-off ADR 0149 already
  named for `folder.read`/`folder.write`, and the same resolution (grant it to "everyone", let a
  teamspace's `inherit=False` isolation do the actual narrowing).
- **Why a second role instead of a permission FIELD on the existing role/assignment**: `permission-
  service`'s model has no notion of "this specific assignment grants a subset of the role's permissions" -
  a role IS its permission list. Two roles, additively assigned, is the model's own native way to express
  "this principal gets set A, and this OTHER principal additionally gets set B" without inventing new
  `permission-service` machinery for a single caller.
- **Why `_grant` needed to become idempotent**: `update_member` can now be called repeatedly with the
  same `can_manage_members` value (e.g. an admin re-saving a form) - without an existing-assignment check
  first, `POST /role-assignments` (which has no uniqueness constraint of its own) would accumulate
  duplicate rows on every repeated call. Checking first, same pattern `dms_permission_client.
  PermissionServiceClient.ensure_role_assignment` already uses, converges safely regardless of how many
  times it's called.
- **Why the naming coincidence with the four-eyes action type `"folder.delete"` is not a conflict**:
  `folder-service` already had an UNRELATED `approval_client.requires_approval("folder.delete")` call
  (the four-eyes principle, action-type namespace, a completely different service/config) using the same
  literal string. Checked explicitly: these are two independent registries (`permission-service`'s
  permission strings vs. `approval-service`'s action types) that happen to share a name under the same
  `<resource>.<action>` convention - harmless, not a technical collision, but worth naming so a future
  reader isn't confused finding the same string doing two different jobs.
- **Why the migration step (`PUT /roles/{id}` against the already-existing "everyone" role) had to be
  applied manually against the live dev stack**: `ensure_everyone_role` is deliberately NOT self-healing
  (documented at its own definition) - exactly the same caveat ADR 0149 already hit for `folder.read`/
  `folder.write`. Applied here the same way a real admin would (`PUT /roles/{id}` with the extended
  permission list, using a principal that already holds `admin.user_management`).
- **Why all four hard-delete call sites, not just the two the plan named**: researching the exact
  location of "forced-purge/trash-expiry-purge" turned up FOUR real call sites of `retention_actions.
  execute_forced_deletion`/`purge_expired_trash_entry`, not two - `reconcile_restore_deletion` (10.4,
  P11-S4) shares the identical `execute_forced_deletion` call and had the identical gap, simply not named
  explicitly in the plan's own shorthand. Fixing all four closes the actual gap completely rather than
  leaving one path silently un-fixed because the plan's wording happened not to enumerate it by name.

## Consequences

- Regression tests: six new `teamspace-service` tests proving the bypass is closed for a non-manager,
  stays open for a manager, and that promotion/demotion/removal keep `teamspace-manager` in sync
  (51/51 passing, was 45); three new `folder-service` tests proving `folder.resource.deleted` is now
  published from the manual-purge, restore-reconciliation, and retention-poll-driven forced-deletion
  paths (143/143 passing, was 140). `permission-service` unaffected structurally, 181/181 still passing.
- The retention poll loop's own trash-expiry purge branch (the fourth call site) shares the exact same
  two-line fix shape as the manual-purge endpoint (call `purge_expired_trash_entry`, then publish both
  events) and is covered by that shared code path being exercised via the manual-purge test - not given
  its own dedicated live-poll-tick test, since triggering a real poll tick end-to-end would need
  manipulating `TrashConfig.restore_period_days`/waiting for the loop's own interval for marginal
  additional coverage over what's already proven.
- Any other installation running this system needs the same one-time `PUT /roles/{id}` migration this
  session applied manually to its own dev stack - `ensure_everyone_role`'s own non-self-healing design
  means a fresh installation gets `folder.delete` automatically (first-ever role creation), but an
  ALREADY-running one does not, until an admin re-applies it (same operational note ADR 0149 already
  left for `folder.read`/`folder.write`).
- `docs/services/folder-service.md` and `docs/services/teamspace-service.md` updated: the
  now-resolved Open Points bullets (folder-service's undocumented purge-path event gap referenced from
  ADR 0154; teamspace-service's own "residual, accepted gap" bullet from ADR 0149).
