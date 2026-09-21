# 0198 — CLI: migration, license, plugin-orchestration, deletion-register commands; storage-service trusted-caller regression fix

**Status:** accepted
**Context:** P67-S2 (Phase 67, "Blocked on X, X Now Exists" — second and last session of the eighth
gap-analysis round's plan). `docs/tools/cli.md`'s coverage table listed migration/transfer triggering
(7.2), license status (9.3), plugin-orchestration status (3.8), and backup/restore + deletion-
reconciliation monitoring (10.4) as "not yet" — but `migration-service`, `license-service`, and
`plugin-orchestration-service` have all existed for many phases with no CLI command ever added for them.

## Decision

**New CLI command modules**, following the existing `commands/<domain>.py` + `register(app)` pattern
(`tools/cli/src/dms_cli/main.py`):

- **`dms migration installations list|pair|unpair`, `dms migration transfers list|get|create`** —
  wraps `migration-service`'s pairing and transfer-triggering endpoints. `pair` warns to stderr that the
  returned `fleet_agent_api_key`-style plaintext key is shown exactly once. `transfers create` reports
  `pending_approval`/`approval_request_id` to stderr when the four-eyes gate on transfer creation defers
  execution.
- **`dms license status`, `dms license upload`** — wraps `GET /license/status`/`POST /license`.
- **`dms plugin-orchestration status`, `dms plugin-orchestration placements list`** — `status` combines
  `GET /plugins` and `GET /nodes` (the two inputs every placement decision is made from) into one command,
  rather than exposing them as two separate reads a human would always run together.
- **`dms deletion-register list|reconcile`** — wraps `document-service`'s and `folder-service`'s
  identical `GET /deletion-register`/`POST .../reconcile-restore-deletion` pair via a `--kind
  document|folder` option, rather than duplicating the module for each service.

**Backup/restore (10.4's other half) is deliberately NOT wired up — the plan's own premise didn't hold
under verification.** `scripts/backup.sh`/`restore.sh`/`test-restore.sh` (documented in
`docs/operations/backup-restore.md`) are pure host-level shell scripts with no HTTP API, no status
endpoint, no database table, and no log a CLI command could realistically read — the CLI only ever talks
to `{gateway_url}/api/{service_type}/{path}`. There is no service to wire this to. `docs/tools/cli.md`'s
coverage table entry is corrected to say so explicitly rather than left implying it's merely unwired.

**Incidentally discovered and fixed: a real regression this round's own earlier P66-S1 session
introduced.** Live-verifying `dms license status` against the real running stack returned a `500`, traced
(via the container's own traceback) to `storage-service` rejecting `license-service`'s `StorageClient`
with `403`. P66-S1 gated `GET /storage/usage` behind `_require_storage_caller` and added the one caller it
had found evidence for at the time (`reporting-service`) — but missed `license-service`'s
`StorageClient.total_bytes()`, an equally real, actively-used caller of the exact same endpoint since
P9-S0 (the `storage_gb` license-usage dimension), which — like `reporting-service`'s own client before
P66-S1 fixed it — never sent an `X-DMS-Principal` header at all. Fixed the same way: `license-service`
added to `storage-service`'s trusted-caller allowlist, `StorageClient.total_bytes()` now sends
`X-DMS-Principal: license-service`.

## Rationale

- **Why correct the backup/restore premise rather than build something**: this project's established
  plan-premise-verification discipline (used throughout the Phase 65+ round) means checking a claimed gap
  against the actual current architecture before acting on it. Inventing a CLI command that reads a file
  from wherever the script happened to run, or fabricating a fake status source, would have been worse
  than not building it — it would imply monitoring capability that doesn't actually exist. Correcting the
  documentation to state the real constraint (no HTTP surface exists) is the honest outcome here, same
  as P67-S1's premise correction for `InstallationManager.tsx` one session earlier.
- **Why fix the `license-service` regression immediately instead of filing it separately**: it was found
  directly by this session's own live-verification step (an explicit requirement of this project's
  Definition of Done for every session) — leaving a freshly-discovered `500` in a core license-status path
  unfixed while moving on would have been worse than the plan's original scope, and the fix is one line in
  each of two files, mirroring the exact precedent (`reporting-service`) already set one session earlier in
  this same round.
- **Why `--kind document|folder` on one module instead of two separate command modules**: the two
  services expose byte-identical endpoints and schemas for this feature (confirmed by the backend code
  itself being a structural copy, `folder_service.main.reconcile_restore_deletion`'s own docstring calls
  it "structurally identical" to `document_service`'s) — a single parameterized module avoids duplicating
  the same six functions twice for no behavioral difference.

## Consequences

- `tools/cli/src/dms_cli/commands/migration.py`, `license.py`, `plugin_orchestration.py`,
  `deletion_register.py` (all new); `tools/cli/src/dms_cli/main.py` registers all four.
- `services/storage-service/src/storage_service/main.py`: `_TRUSTED_STORAGE_CALLERS` extended with
  `"license-service"`.
- `services/license-service/src/license_service/clients.py`: `StorageClient` now sends
  `X-DMS-Principal: license-service` on `total_bytes()`.
- `docs/tools/cli.md` updated: migration/license/plugin-orchestration/deletion-register rows closed;
  backup/restore row corrected to state no HTTP surface exists rather than "not yet wired."
- New tests: `tools/cli` +16 (mocked, one per new subcommand plus an invalid-`--kind` rejection test),
  90/90 total. `storage-service` +1 (`test_license_service_accepted_as_trusted_caller`), 167/167.
  `license-service` +1 new file `test_storage_client.py`
  (`test_storage_client_sends_a_trusted_principal_header`, a real HTTP round-trip against
  storage-service, not mocked — every existing license-service test mocks `StorageClient` entirely and
  would never have caught this class of bug), 38/38.
- `storage-service`/`license-service` rebuilt/redeployed (the new `license-service` test initially failed
  against the live, not-yet-rebuilt containers, same pattern as P66-S1's own `reporting-service` fix —
  confirmed by the rebuild fixing it).
- **Live-verified against the real running stack** via a real `dms login` + real command invocations (not
  the mocked test suite): `dms plugin-orchestration status` and `placements list` render real registered
  plugins/nodes/placement decisions; `dms migration installations list`/`transfers list` render real
  paired installations/transfers; `dms deletion-register list` reaches the endpoint cleanly (empty result,
  no entries currently exist); `dms license status` — failed on the first attempt with the regression
  described above, fixed, rebuilt, re-ran, now returns the real license snapshot correctly.
