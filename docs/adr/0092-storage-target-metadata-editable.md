# 0092 — storage-service: target metadata (object_lock_mode/role) live-editable, target-set structure stays env-var-only

**Status:** accepted (Post-roadmap Phase 22 Session 7)
**Context:** Post-roadmap Phase 22 Session 7, affects `storage-service`, `admin-ui`

## Decision

The plan wording for this session ("make OCR/storage target set editable — extension of the same two
existing pages from P22-S6") was ambiguous: "target set" is, in the existing code/docs, exclusively a
`storage-service` concept — `ocr-service` has no equivalent. Clarified before implementation via
`AskUserQuestion` rather than assumed: scope = make **only** `object_lock_mode`/`role` per already-
configured target editable (extension of `StorageGuard`/`/storage-guard/`, the page P22-S6 referenced as
its UI model) — **no** credentials, **no** structural CRUD, **no** "OCR target set" (doesn't exist).

1. **New, sparse DB table `target_override`** (`target_id` PK, `object_lock_mode`, `role`,
   `updated_at`) — unlike `OperationalConfig`/`GuardConfig` (P22-S6, singleton with get-or-create), only a
   target that has actually been overridden gets a row at all; if none exists, the env-var value from
   `Settings.targets` continues to apply unchanged.
2. **New endpoint `PUT /guard-status/{target_id}/config`** — `404` for an unknown `target_id` ("edit
   existing entries only", same requirement as P22-S6), `422` if the change would leave NO regular
   (non-archive) target remaining (a newly found security gap, see "Rationale").
3. **`_compute_target_state()`** (new pure function in `main.py`) merges `Settings.targets` with all
   `target_override` rows into an effective `BackendTargetConfig` list — called at startup AND on every
   `PUT`, with the result written straight back into `app.state.target_configs`/`.targets`/
   `.archive_targets`/`.lock_target_ids` (live reload without every individual read site elsewhere in the
   code having to read fresh from the DB itself).
4. **`resolve_targets()`/`resolve_archive_targets()`** (`backends/__init__.py`) now take a
   `list[BackendTargetConfig]` instead of reading `Settings` directly — callers pass, depending on
   context, either the structural env-var list (startup/healthz) or the live-merged list (`PUT` handler).
5. **`admin-ui`**: `StorageGuard.tsx` (`/storage-guard/`) gets two new columns with checkboxes
   ("Governance Mode", "role=archive") in place of the previous read-only "Object Lock" column — a click
   immediately calls `PUT .../config` for that specific target and reloads.

## Rationale

- **Why sparse instead of singleton**: `target_override` has (unlike `OperationalConfig`) no meaningful
  global default values that would need to be seeded on the very first row — each target is independent,
  and most installations will never set an override. One row per actually changed target is simpler than
  a singleton with a JSON dict field.
- **Why `app.state` is recomputed on every `PUT` rather than reading fresh from the DB at every read
  site** (unlike P22-S6's `OperationalConfig`, which is read fresh on EVERY affected request):
  `object_lock_mode`/`role` are read at roughly 15 places in the code (upload routing, archive routing,
  retention guard, lock status displays) — a change of that scope at every single site would have
  significantly increased the risk of missing one. Recomputing at `PUT` time (analogous to
  `app.state.backends`, which is likewise computed once) achieves the same live-reload result (a single
  in-process state is immediately visible to ALL subsequent requests) with a much smaller, lower-risk
  diff. Trade-off: with multiple horizontally scaled `storage-service` replicas, other instances only see
  the change on their own next `PUT` call or restart — documented as a known limit (see "Consequences").
- **Why `PUT .../config` rejects a change that would leave no regular target remaining** (a real error
  condition found while implementing, not part of the original request): `upload_object` uses
  `app.state.targets[0]` as the primary target — if `app.state.targets` became empty after a
  `role="archive"` change (with only one target configured, as in the dev/test setup), EVERY subsequent
  upload would crash with an unhandled `IndexError` (ultimately `500`) instead of a meaningful error. The
  new check computes the target list ahead of the actual write, as a trial, and rejects with `422` before
  the invalid state is ever persisted.
- **Why no re-check of quorum satisfiability on a `role` change**: `PUT /operational-config` (P22-S6)
  already validates `quorum_count` against the CURRENT target count at the time it's set. A `role` change
  could subsequently invalidate that check (fewer regular targets than the previously set `quorum_count`)
  — deliberately not addressed in this session (a smaller, rarely occurring edge case than the "target
  list completely empty" crash risk above), documented as a known gap.

## Consequences

- **Migration**: none (one brand-new table).
- **Known limit under horizontal scaling**: multiple replicas of this service do NOT share `app.state` —
  a replica that has had no `PUT` call or restart of its own since a change won't see the new values.
  Uncritical for this project's current single-replica deployment reality, documented for a future
  multi-replica session.
- **Known gap**: `quorum_count` (P22-S6) is not re-validated against the target count on a `role` change
  (see "Rationale").
- **Test infrastructure finding** (found proactively, before it caused a failure): `storage-service`'s
  `tests/conftest.py` already had a teardown `DELETE` list (unlike the other services in this session,
  which had NONE at all) — that list was missing an entry for `operational_config` (P22-S6) AND the new
  `target_override` table. Both added, verified by running three times in a row.
- **Tests**: `storage-service` 122 (previously 117, +5: `404` for an unknown target, `422` for
  "would leave no regular target" with the only test target, an end-to-end proof via an object uploaded
  WITH `retain_until` BEFORE the override, which afterward stands under governance lock without a
  restart, plus two repository unit tests). `admin-ui` 204 (previously 201, +3:
  `storage-guard.test.tsx` extended for toggling object-lock mode/disposal role and error display).
- **Verified live against the actual running stack** (image rebuild + restart of
  `storage-service`/`admin-ui`): `PUT` on an unknown target → `404`; `PUT role=archive` on `local`
  (the only regular target configured in the dev stack) → `422`; a real object with `retain_until` was
  uploaded BEFORE enabling `object_lock_mode=governance`, after which governance mode was enabled live
  (`GET /guard-status` confirmed it immediately), a deletion attempt without bypass returned `403` —
  exactly the live-reload behavior with no restart between upload and lock activation. Governance mode
  was then reset; the test object itself remains locked, under the independent, backend-type-independent
  application-layer guard, until its retention period naturally expires (~24h) (no `dms-admin` realm-role
  account available for a `bypass_governance` deletion attempt in this session) — harmless, clearly
  labeled as a test artifact. No interactive browser test of the UI change (no browser/Playwright
  available in this development environment, project-wide established practice).
- Docs: new [ADR 0092](0092-storage-target-metadata-editable.md), `docs/services/storage-service.md`
  (API table, new section, tests section, "Open Points"), `docs/services/admin-ui.md`
  ("Storage Guard" section updated, backend integration table, tests section) added.
