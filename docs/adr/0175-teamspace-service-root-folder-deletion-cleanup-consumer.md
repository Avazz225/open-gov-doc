# 0175 — `teamspace-service` reacts to `folder.resource.deleted` for its root folder instead of staying permanently orphaned

**Status:** accepted
**Context:** P55-S1 (Phase 55, "Correctness: Cross-Service Cleanup on Delete Paths" — first session of
the fifth gap-analysis round's second phase). Found by this round's live-code security sweep:
`teamspace-service` has **no `consumer.py` at all** — confirmed no file, no `subscribe()` call anywhere
in its source. Per ADR 0163/P44-S2, a `teamspace-manager` legitimately holds `folder.delete` on a
teamspace's root folder — a deliberate decision at the time, correctly gating *who* can trigger this, but
`folder-service` itself has no concept of "teamspace" and ADR 0163 explicitly names that as unaddressed
scope. This means a manager CAN delete/trash the root folder directly via `folder-service`, entirely
bypassing `teamspace-service`'s own `DELETE /teamspaces/{id}` (whose docstring already states the
intended path deliberately preserves the root folder — see `repository.delete_teamspace`'s docstring,
"Deliberate boundaries").

The result before this session: the `Teamspace` row, its `TeamspaceMember`/`TeamspaceAppointment`/
`TeamspaceContact` rows, and every member's `permission-service` role assignment on the now-nonexistent
`root_folder_id` all kept existing forever — a silently broken teamspace with no cleanup path and no
visible error until a member tried to use it (every folder-scoped operation would 404).

## Decision

**Add a `consumer.py` subscribing to `folder.resource.deleted`, keyed on `root_folder_id`. On receipt:
revoke every member's `permission-service` access on that resource (member + manager roles), then call
the same `repository.delete_teamspace` the manual `DELETE /teamspaces/{id}` path already uses — full
teardown, not a soft "mark orphaned" flag.**

## Rationale

- **Why `folder.resource.deleted` specifically, not `folder.trashed`**: `folder.trashed` is a reversible
  soft-delete — a manager could trash the root folder and restore it later, and treating a trash as
  immediate teamspace teardown would destroy a teamspace that was never actually, permanently gone.
  `folder.resource.deleted` is the structural "this folder is now permanently gone" signal (fired by
  every hard-delete path, including the forced-deletion/retention paths that also fired it as of Post-
  Roadmap Phase 44 Session 2) — the correct trigger for a decision this destructive. A folder that is only
  trashed leaves the teamspace unreachable but NOT torn down; if the manager restores it, nothing was ever
  broken on this service's side, since this event never fired.
- **Why full teardown rather than a "mark orphaned, surface in admin-UI" flag**: a lighter-weight
  alternative would keep the row and show a warning badge somewhere in `admin-ui`, letting an operator
  decide what to do. Rejected as materially larger scope for this session (new admin-UI surface, new
  state field, new decision UI) for a case that has exactly one meaningful recovery action once the root
  folder is structurally gone: nothing — the teamspace's entire reason to exist (its shared folder) no
  longer does, unlike, say, a trashed document that can be restored. Full teardown matches the simpler,
  already-established precedent this project uses for exactly this class of bug (e.g. ADR 0169's
  pseudonymization-vault cleanup on document hard-delete: delete the dependent rows, no orphan-flag
  intermediate state).
- **Why revoke role assignments before deleting the teamspace row, not after or not at all**: mirrors
  exactly what the manual `DELETE /teamspaces/{id}` path already does (revoke each member's resource +
  manager access, then delete) — the only difference here is skipping `restore_default_inheritance`
  (meaningless for a folder that no longer exists) and reacting to an event instead of a direct API call.
  Leaving the role assignments would be the identical "stale grant on a resource nobody can reach again"
  shape this whole gap-analysis round flagged as a data-hygiene issue elsewhere (see P55-S2,
  `auth-service`'s equivalent gap for deleted users).
- **Why this doesn't need to distinguish who deleted the folder**: the fix closes the STRUCTURAL gap (a
  folder gone means the teamspace built on it cannot function), independent of by which path it became
  gone — a manager going through `folder-service` directly, an admin force-deleting it, or a future
  retention-triggered deletion would all correctly trigger this same cleanup.

## Consequences

- `teamspace-service` gains a second, consumer-side `NatsEventBusClient` connection alongside its
  existing producer (same dual-bus pattern `case-service`/`notification-service` already use) — `settings.subjects
  = ["folder.resource.deleted"]`.
- New `repository.get_teamspace_by_root_folder_id(session, root_folder_id) -> Teamspace | None` — the
  handler's lookup key; returns `None` (a silent no-op) for any `folder.resource.deleted` event whose
  subject isn't a teamspace root folder, which is the common case (this event fires for every folder
  deletion in the installation, not just teamspace roots).
- New tests: a consumer-level test proving the handler tears down a real teamspace + revokes its members'
  access on receipt of the event, and that an unrelated folder's deletion event is correctly ignored (no
  matching teamspace, no error).
- Live-verified against the real running stack: created a real teamspace via the API, deleted its root
  folder directly via `folder-service` (bypassing `teamspace-service` entirely, reproducing the exact
  bypass this session closes), confirmed the real, rebuilt `teamspace-service` container's consumer picked
  up the event and the teamspace row + its member's role assignment were both gone afterward — where
  before this session, both would have persisted forever.
