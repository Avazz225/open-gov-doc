# Backup & Restore (Concept 10.4, P11-S3/S4)

Concept 10.4 requires coordinated backup of the shared DB, storage backends, and
configuration, a fixed restoration sequence (including mandatory deletion
reconciliation), and a maintenance mode during the process. This page describes the
mechanism as actually implemented across both sessions.

## Architecture decision at session start

Two open questions were clarified before implementation:

1. **Operational scripts instead of a permanently running service**: `scripts/backup.sh`/
   `scripts/restore.sh`, no new `backup-service` with standing privileged
   Postgres/storage credentials — the same pattern as `scripts/rolling-update.sh`
   (P10-S3) and the deliberate avoidance of the Docker socket from P10-S1.
2. **Scope boundary P11-S3/P11-S4**: the roadmap already cleanly separates the
   backup/restore *mechanism* (P11-S3) from **deletion reconciliation after
   restore** + **automated restore tests** (P11-S4, see below). P11-S3
   verifies the mechanism for real, but against an isolated scratch environment — not
   against the running dev stack (the only shared Postgres instance of all 26
   services; an actual overwrite would be destructive to the running session).
3. **(P11-S4) Depth of the automated restore test**: a split approach (recommendation)
   instead of a full shadow application stack — see "Deletion reconciliation after
   restore" below.

## One correction relative to the original plan: no maintenance mode during backup

Concept 10.4's own text ties maintenance mode explicitly only to **restore**
("automatically trigger a maintenance mode during restore ... regular user access
remains locked until deletion reconciliation is complete"), not to the backup itself. A
WAL-based backup (`pg_basebackup` + continuous WAL archiving) is also
by design an **online/hot backup** — consistency arises from WAL replay during
restoration, not from freezing write access during the backup. An
originally planned brief maintenance-mode trigger during `backup.sh` was therefore
dropped again (see PROGRESS.md) — it would also have posed a real obstacle:
`POST /maintenance-mode/lift` (permission-service) mandatorily requires the currently
**activated superuser** (4.8), whose activation in turn goes through a real
break-glass four-eyes procedure (4.6) — disproportionately heavyweight for
a routine backup operation. Since `restore.sh` in this session works exclusively
against an isolated scratch environment (see below), it likewise does not touch the
running stack and consequently does not need maintenance mode either. An actual
production restore (P11-S4 context) would need it — documented as an open
follow-up point.

## What is backed up

- **Shared database**: `infra/docker-compose.yml`'s `postgres` service now has
  continuous WAL archiving (`archive_mode=on`, `wal_level=replica`,
  `archive_command='test ! -f /wal-archive/%f && cp %p /wal-archive/%f'`, volume
  `postgres-wal-archive`). A new `postgres-wal-archive-init` service reproducibly
  fixes the default root ownership of a freshly created
  Docker volume (a real failure discovered live on the first start: `cp: can't
  create '/wal-archive/...': Permission denied`) — `postgres` now waits for it
  (`depends_on: condition: service_completed_successfully`).
- **Storage backends**: `scripts/backup.sh` reads `storage-service`'s `DMS_TARGETS`
  and backs up every target with `role != "archive"` (the archive role, 5.6, serves
  transfer to the archive, not protection against logical errors — deliberately a different purpose).
  Only `type="local"` is implemented and verified in this session (the
  target type actually active in this stack right now); `type="s3"` is recognized
  but skipped and reported as a warning — a documented gap, not
  silently missing behavior.
- **Configuration**: **no new mechanism**. Practically all runtime
  configuration already lives in Postgres singleton tables
  (`TrashConfig`/`RetentionConfig`/`UploadConfig`/`sensor_config` among others) and is
  thus automatically part of the DB backup. The *independent*
  configuration export intended by the concept needs 7.3 (configuration import/export
  service, only exists from P12-S3 on, the same backward-dependency pattern as
  the P10-S0 finding on 10.1) — `manifest.json` already carries a `config_export: null`
  placeholder field for this.
- **Keycloak**: unchanged, automatically captured (uses the same Postgres instance).

## `scripts/backup.sh`

```bash
scripts/backup.sh [--dest DIR]   # Default: ./backups/<timestamp>/
```

Flow: back up storage targets (`docker exec storage-service tar czf - -C <base_path> .`)
→ `pg_basebackup` against the running Postgres container (online) → record the current
WAL LSN (`pg_current_wal_lsn()`, the "consistency anchor" from 10.4) → write
`manifest.json`. `manifest.json` is the single source of truth for which artifacts
belong together — exactly what 10.4 names as the main source of error ("a restore to
temporally different states of storage and DB").

## `scripts/restore.sh`

```bash
scripts/restore.sh <backup-dir> [--recovery-target-time "2026-08-08 17:30:00+00"]
```

Covers steps 1-3 of the 10.4 sequence, against an **isolated
scratch environment**, not the running stack:

1. Unpack storage tarballs into a temporary directory.
2. Build a second, temporary Postgres container (`dms-postgres-restore-test`, its own
   scratch volume, no `dms-net`, no host port) from the base backup, with
   `recovery.signal` + `restore_command`/`recovery_target_time`/
   `recovery_target_action=promote` — real point-in-time recovery via the
   WAL archive volume (mounted read-only).
3. Check storage checksums from the **restored** `storage.object_metadata` table
   against the unpacked files — proves that the DB and storage restore
   are indeed consistent with each other.
4. Scratch containers/volumes/directories are always cleaned up at the end (`trap`).

## Deletion reconciliation after restore (step 4, Concept 10.4, P11-S4)

The core risk: a restore to a point in time *before* a forced deletion (5.2a) that has
since taken place would unintentionally "resurrect" the document/object that was
supposed to be deleted.

- **Deletion register ledger, independent of the DB restore cycle**: `audit-service`'s
  existing NATS consumer appends a line to an append-only file
  (`/deletion-ledger/deletion-register.jsonl`) on a **dedicated Docker volume**
  (`deletion-ledger-data`) on every `document.force_deleted`/
  `folder.force_deleted` event — technically completely separate from the Postgres
  instance that `backup.sh`/`restore.sh` back up/restore. This is the only way the
  register stays "kept up to date" (10.4 literally), even when the DB itself is
  reset to an earlier point in time. Deliberately only `forced_deletion` events
  (5.2a) — regular `trash_expiry` deletions have, per the concept, a lower
  consequence and are not part of this ledger.
- **Detection, actually run against the scratch DB**: after point-in-time recovery,
  `scripts/restore.sh` reads the ledger, filters entries between the backup timestamp
  (`manifest.json`) and the actual, real moment of the restore run, and checks
  per entry via SQL query against the restored scratch DB whether the object
  still/again exists there. Hits are reported explicitly, including the exact
  `curl` call for the actual re-run.
- **Re-execution, actually run against the live stack**: `POST /documents/{id}/reconcile-restore-deletion`
  (document-service) and `POST /folders/{id}/reconcile-restore-deletion` (folder-service)
  — invokes "the same mechanism as during the original forced deletion" again (10.4 literally,
  `retention_actions.execute_forced_deletion()`), with `triggered_by=
  "system:restore-reconciliation"` and an additional event payload field
  `reconciliation_of_entry_id`. Gate: `X-DMS-Roles` must contain `dms-admin`.
- **Split test approach** (open question at session start): the *detection* runs
  fully automated and for real against the isolated scratch environment
  (`scripts/test-restore.sh`); the *actual re-deletion* is verified separately and
  for real against the running live stack (gate/success/audit entry), instead of
  standing up a complete second shadow application stack (document-service+storage-service
  against scratch data) — both pieces are genuinely tested, just not chained
  together in a single continuous run.

## `scripts/test-restore.sh` (automated restore test, P11-S4)

```bash
scripts/test-restore.sh
```

A self-verifying, repeatable test run (exit code, not just log output):
creates a test document, takes a backup, then triggers a real forced deletion
(retention period set into the past + a forced poll tick via
container restart), restores to a point in time *before* this deletion, and checks
that `restore.sh` actually detects the case. Afterward, separately verifies the
reconcile endpoint against a second, active test document (gate 403/204, real
deletion, real audit entry).

## Deliberately not part of P11-S3/S4

- **Search index rebuild (step 7)**: would require fully reconnecting the app stack
  to a restored DB (the search index itself needs no
  dedicated backup, since it is reconstructible) — open follow-up point for a
  possible later session.
- **Actual automatic execution of reconciliation directly from `restore.sh`**:
  would require a running document/folder-service against the restored DB,
  which deliberately does not exist in the isolated scratch environment —
  `restore.sh` instead prints the exact call for the live system.
- **Event bus reset (step 5) / registry restart (step 6)**: not applicable for an
  isolated scratch verification (no NATS/no app services involved) — only
  documented in the concept's sequence, not scripted.
- **Maintenance mode during an actual production restore**: see the section above —
  needed by an actual restore operation, but not by the
  script-driven scratch verifications of this project.
- **Automated/recurring (scheduled/cron/CI) restore tests**: `test-restore.sh`
  is a repeatable, self-verifying run, but without cron/CI integration in
  this session.
- **`type="s3"` storage targets**: recognized, but not backed up/restored — not
  actively configured in the current stack anyway.
- **Reconciliation for `trash_expiry` deletions and transfer to the archive (5.6)**: Concept 10.4
  names both as analogous but less severe cases - not part of this session.
