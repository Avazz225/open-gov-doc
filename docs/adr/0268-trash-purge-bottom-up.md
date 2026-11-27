# 0268 — Trash expiry removes folders bottom-up, a parent only after its children

**Status:** accepted (P98-S1)
**Context:** folder-service's hourly retention tick had failed since at least P95-S2a. The log shows:
`update or delete on table "folder" violates foreign key constraint "folder_parent_id_fkey"`, at
`purge_expired_trash_entry` → `hard_delete_folder`.

- Trashing a folder gives its whole active subtree the same `deleted_at` (`soft_delete_folder`), so a
  parent and its subfolders expire together.
- `list_expired_trash` returned them in no particular order. When the parent came first, its delete hit
  the self-reference `parent_id`, the exception left the loop, and every later entry of that tick was
  skipped too. The next tick failed the same way.
- A subfolder under legal hold is left out of `list_expired_trash` on purpose. Its parent could then
  never be removed.

The manual purge (`POST /folders/{id}/purge`) already refuses a folder with subfolder rows (`409`).

## Decision

1. **Deepest first.** `purge_expired_trash` orders the expired entries by depth (`order_deepest_first`)
   and removes children before their parents within one tick.
2. **A parent is only due when every child is.** A folder that still has a subfolder row when its turn
   comes is deferred and logged (`trash_purge_deferred`). This covers a child under legal hold and a
   child trashed later on its own with a later deadline. It is removed in the first tick after the child
   is gone. Nothing is cascaded past a hold.
3. **One entry, one transaction.** Each removal commits on its own, as before. A failing entry is now
   rolled back and logged (`trash_purge_failed`), and the tick goes on with the rest.

Rejected: deleting the whole subtree when the parent expires. That would remove a held child with its
parent, which is exactly what a legal hold forbids, or delete a child before its own restore deadline.

## Decision 4 — permission-service removes what points at a deleted resource

Deploying the fix purged 497 long-stuck folders in the first tick. Their `folder.resource.deleted` events
exposed a second, older defect:
- `role_assignment.resource_id` and `scope_lock.resource_id` reference `resource_node` without
  `ON DELETE CASCADE`;
- the structure consumer's plain node delete failed for every folder that still carried an assignment,
  and JetStream redelivered it forever (over 4,500 failures in five minutes);
- the failing events blocked every later structure event, so new folders stayed unregistered and
  folder-service's tests got `403`.

`repository.delete_resource_subtree` now deletes the assignments and scope locks of the node and of all
its descendants, then the node. The descendants' nodes go with `parent_id`'s existing cascade. What was
removed is logged with the principals (`resource_deleted_with_dependents`).

**Scope locks are otherwise never hard-deleted**, so their history stays auditable. For a resource that
no longer exists, a row can't stay without the node. The history remains in the audit trail through the
lock's own create/release events.

## Consequences

- **Live (P98-S1):**
  - the first tick after deployment purged 497 stuck trash folders (497 `trash_expiry` entries in the
    deletion register), with no tick error and nothing deferred;
  - after the permission-service fix the redelivered events cleared (0 FK errors), 454 of them with
    assignments removed;
  - folder-service and permission-service tests are green again.
- A parent waiting for a held child stays in the trash past its restore period. It is no longer
  restorable (the period is over) but isn't lost either; the deletion register records it once it is
  removed.
- **Not changed:** documents in a trashed subtree are purged by document-service on its own schedule;
  there is no cross-schema FK between the two. A document under hold keeps pointing at a folder id that
  may already be gone, as before P98. If that matters, it needs its own decision.
- Tests: bottom-up across three levels, a parent deferred behind a held child, a failing entry skipped
  (folder-service, 3); subtree delete with assignments and a lock, unknown resource (permission-service,
  2).
