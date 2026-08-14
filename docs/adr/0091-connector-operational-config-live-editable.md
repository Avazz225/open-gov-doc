# 0091 — storage-service/signature-service: operational parameters live-editable, target/connector list stays env-var-only

**Status:** accepted (Post-roadmap Phase 22 Session 6)
**Context:** Post-roadmap Phase 22 Session 6, affects `storage-service`, `signature-service`, `admin-ui`

## Decision

`storage-service`'s target set (`Settings.targets`, including S3 credentials `access_key`/`secret_key`)
and `signature-service`'s connector set (`Settings.signature_providers`) were previously plain Pydantic
settings from env vars, read directly at many points in the code, changeable only via a restart. This
session makes a deliberately narrowly scoped part of that live-editable via new `GET`/`PUT
/operational-config` (`storage-service`) and `GET`/`PUT /signature-config` (`signature-service`)
endpoints — following the same get-or-create singleton pattern as `OcrConfig`/`GuardConfig` (read fresh
from the DB on every access, no `app.state` cache, hence effective without a restart):

1. **`storage-service`**: `write_strategy`, `quorum_count`, `max_replication_attempts` — plain operational
   parameters with no secrets involved.
2. **`signature-service`**: `levels` per already-configured connector — likewise without secrets
   (certificates/keys themselves live in `InternalCa`, untouched by this session).
3. **Deliberately NOT live-editable** (stays env-var-only, changeable only via restart): the target/
   connector **list** itself (`id`, `type`, `base_path`/`endpoint_url`/`access_key`/`secret_key`/
   `bucket`/`region` for storage targets; `id`, `type` for signature connectors), as well as
   `object_lock_mode`/`role` per storage target.
4. **New admin UI pages**: `/storage-operational-config/` (form) and `/signature-config/`
   (table with level checkboxes per connector).

## Rationale

- **Why credentials/structure are deliberately left out of scope** (user requirement for this session,
  clarified via follow-up question): making `access_key`/`secret_key` live-editable would have required
  new encryption/masking infrastructure (plaintext must never appear in a `GET` response) — a
  substantially larger, more security-critical change than the rest of this session.
  `object_lock_mode`/`role` are WORM-/records-disposal-relevant (5.1/5.2a/5.6) — an accidental live change
  could have compliance-relevant consequences (e.g. a governance target that suddenly no longer enforces
  object lock). Both deliberately stay restart-only until a future session tackles that dedicated and with
  its own care.
- **Why "edit existing entries only", no CRUD management of the list** (user requirement): the set of
  configured targets/connectors is structurally tied to actually existing infrastructure (an `id` without
  a corresponding real backend instance would be meaningless) — creating/removing stays a deployment
  operation (env var + restart), not an admin UI click. `PUT /operational-config` has no list anyway (only
  scalars); `PUT /signature-config` rejects unknown connector `id`s with `422`.
- **Why live reload instead of "takes effect only after restart"** (user requirement): matches the
  already-established expectation for an admin UI settings page in this project (`OcrConfig`,
  `GuardConfig`) — an admin who changes a value expects it to take effect, not to have to remember a
  service restart.
- **Why `write_strategy`/`quorum_count`/`max_replication_attempts` are read fresh from the DB on every
  affected request instead of being cached in `app.state`**: exactly the already-established pattern of
  `GuardConfig` in this same service (`repository.get_guard_config`, read fresh every time) — one
  additional, indexed primary-key read per affected request is the accepted price for live reload without
  its own invalidation logic.
- **Why the quorum-satisfiability check (`_validate_settings`) is repeated on `PUT /operational-config`**:
  the target count is structurally fixed (env var, unchanged by this session) — otherwise an admin could
  live-set a `quorum_count` that no actual write operation could ever satisfy, unnoticed until the next
  upload failure.
- **Why `signature-service`'s validation (`levels` not empty, `type=internal` no QES) is duplicated in the
  repository instead of reused from `SignatureProviderConfig._check_levels`**: the Pydantic
  `model_validator` is bound to instantiating a `SignatureProviderConfig` object (settings schema
  context), whereas the runtime check needs the same rule independent of a concrete Pydantic model
  instance (just `id`+`levels`+`type` as loose values). Both spots are short enough that a shared
  extraction would have added more indirection than benefit.

## Consequences

- **Migration**: none (two brand-new tables `storage.operational_config`/
  `signature.signature_config`, `Base.metadata.create_all` creates them automatically).
- **Test infrastructure finding**: `signature-service`'s `tests/conftest.py` truncate list was missing the
  new table (fixed, same finding as already seen in P22-S2 for `permission-service`). `storage-service`
  has NO truncate fixture at all (existing tests instead rely on per-test-unique object keys) — the new
  tests that mutate the DB singleton `operational_config` therefore got their own, local restore fixture
  (`operational_config_client`) that restores the env-var defaults after each test, rather than reworking
  the entire service's test infrastructure. Both fixes were verified by running the respective test suite
  twice in a row (the exact symptom that would otherwise only have surfaced on a second, independent test
  run).
- **Tests**: `storage-service` 117 (previously 113, +4: `GET` default, `PUT` persistence, quorum
  rejection, end-to-end proof via a real upload after `PUT`); `signature-service` 16 (previously 11, +5:
  `GET` default, three validation cases, end-to-end proof via a failing AES sign attempt after removing
  AES from the levels, followed by a successful SES attempt). `admin-ui` 201
  (previously 191, +10: `storage-operational-config.test.tsx` 4 tests, `signature-config.test.tsx` 6 tests).
- **Verified live against the actual running stack** (image rebuild + restart of
  `storage-service`/`signature-service`/`admin-ui`): `GET /operational-config`/`GET /signature-config`
  showed the correct env-var starting values; a `PUT` with `quorum_count=2` (only 1 regular target
  configured in the dev stack, the second carries `role=archive`) returned `422`; a satisfiable `PUT` to
  `strategy=quorum` followed by a real object upload ran successfully through the quorum code path,
  entirely without a restart between `PUT` and upload; `signature-config`'s `PUT` with `levels=["qes"]`
  for the `internal` connector, and with an unknown connector `id`, both returned `422`. All test
  data/configuration changes were subsequently reset. No interactive browser test of the two new admin UI
  pages (no browser/Playwright available in this development environment, project-wide established
  practice) — instead covered via Vitest component tests plus the backend API verification above through
  the exact same gateway calls.
- Docs: new [ADR 0091](0091-connector-operational-config-live-editable.md),
  `docs/services/storage-service.md`, `docs/services/signature-service.md` (each with API table, new
  section, tests section), `docs/services/admin-ui.md` (page table, new section, backend
  integration table, tests section) added.
