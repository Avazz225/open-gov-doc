# 0002 — Conflict protection for force-unlock via optimistic version checking instead of a "monitored" lock state

**Status:** accepted
**Context:** Concept 4.2 (document lock during editing, in particular "conflict handling on force-unlock"), Session P3-S2 (Document Service)

## Decision

The concept describes a three-valued lock state for force-unlock: normally
locked → administratively released, but **"monitored"** → the original
editor is recognized as a conflict case at their next check-in attempt based
on this monitored state.

The Document Service instead implements **no third lock state**. Force-unlock
deletes the lock entirely (`repository.force_release_lock`). The actual
protective effect comes from a **lock-independent, always-active optimistic
conflict detection mechanism** at check-in time: every version upload must
supply `expected_base_version_number` (the version the edit was based on). If
this value differs at execution time from the document's actual current main
version, the upload is not merged in as an overwrite, but is stored as a
separate, still-retrievable **conflict copy** alongside the current version
(`<name>_conflict_<user>_<timestamp>`), without moving the main-version
pointer (see `repository.checkin_version`).

## Rationale

Both models fulfill the guarantee required by the concept identically: **the
original editor must never silently lose work.** The difference lies only in
*where* the detection happens:

- Concept variant: at the lock object itself (a third state "released, but
  monitored" plus special-case logic that checks for exactly this case at the
  next check-in of the *original* holder).
  Conflict avoidance results independently of any lock.
- Chosen variant: at the version chain itself. A check-in is a conflict
  exactly when its base version is no longer the current one - regardless of
  *why* that is the case (force-unlock, expired timeout, no lock taken at
  all). Force-unlock does not need to leave behind a special state for this;
  it only needs to actually release the lock so that another user can check
  in normally.

Advantages of the chosen variant:

1. **A single mechanism instead of two**: The conflict-copy logic protects
   not only the force-unlock case, but every conceivable race (e.g. two
   check-ins shortly after each other without either ever taking a lock, or
   an expired timeout unlock). The concept describes the force-unlock case
   only as an example of a more general problem - the implementation covers
   the more general problem directly.
2. No additional state machine on the lock (active → monitored → gone) that
   would need to be maintained and tested correctly on its own.
3. Matches the established optimistic-concurrency/ETag pattern from
   WebDAV/CMIS, which is how the document is addressed anyway by external
   applications (4.2 names Word over WebDAV/CMIS as an example).

## Consequences

- The force-unlock endpoint (`POST /documents/{id}/lock/force-release`)
  itself does **not** trigger a notification/audit event referencing a later
  conflict copy - it only publishes `document.lock.force_released` with the
  original holder, as soon as the release happens. The actual conflict copy
  (if one arises later) separately generates its own
  `document.version.created` event with `is_conflict: true`. Together, the
  two events yield the same traceability in the audit trail (the Audit
  Service consumes `document.>`, see the P3-S2 change to its `subjects`) as
  required by the concept, just via two separate events instead of one linked
  event.
- A four-eyes principle for force-unlock (4.3) is not yet wired up - it
  follows with the generic approval mechanism in P6-S4.
- `based_on_version_number` is stored both on `DocumentLock` and on each
  `DocumentVersion`, even though the lock's value is currently not evaluated
  for conflict detection (which is based purely on the value passed at
  check-in) - it serves traceability ("what was this lock based on") and
  could be used in a later session for stricter checks (rejecting check-in
  without an active lock), should that prove necessary.
