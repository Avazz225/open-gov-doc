# 0030 — Storage WORM: application-layer guard + S3 Object Lock in Governance mode

**Status:** accepted
**Context:** Concept 5.1/5.2a ("retention periods"/"forced deletion"), Session P7-S1 — retention/legal hold/forced deletion including the deletion register

## Decision

`storage-service` gets a two-tier WORM protection instead of a single, backend-specific mechanism:

1. **Application-layer guard** (`retention_guard.py`, same pattern as [ADR 0017](0017-storage-device-identity-guard.md)'s `identity_guard.py`): every `ObjectCopy` gets an optional `retention_until` date, independent of backend type. `DELETE /objects/{key}` checks before every deletion whether a target with `object_lock_mode="governance"` has a copy with `retention_until` in the future — if so, deletion is only possible with `?bypass_governance=true` **and** a role from `Settings.governance_bypass_role` (default `dms-admin`, checked via the `X-DMS-Roles` header injected by the gateway), otherwise `403`.
2. **Real S3 Object Lock as additional hardening** for `type="s3"` targets with `object_lock_mode` set: `write()` sets `ObjectLockMode="GOVERNANCE"`/`ObjectLockRetainUntilDate`, `delete()` uses `BypassGovernanceRetention=True` for the authorized bypass. For `type="local"` targets, only the pure application-layer check applies — an honestly documented limit, not a pretended protection where there is no technical equivalent.

**Deliberately only `"governance"` is a valid value for `object_lock_mode`** (no `"compliance"` in the schema). Compliance mode would make the sanctioned forced-deletion exception (5.2a) required by the concept itself technically impossible — even an AWS root account cannot lift a compliance-mode lock before it expires.

**No automatic bucket migration**: `ObjectLockEnabledForBucket=True` is only set in the `create_bucket` branch of `ensure_bucket()` (newly created bucket). For the already-existing, actively used dev bucket, the `head_bucket` success branch remains a pure no-op — S3 Object Lock cannot be enabled on an existing bucket after the fact, and an automatic intervention (re-creation + data migration) would be risky and was outside this session's scope. Verification is done via an additional, purely test-purpose second target with a fresh bucket (same approach as in P5c-S2).

## Rationale

- **Portability before completeness**: a pure S3-Object-Lock approach would have no equivalent for `local`-backend targets (NFS/PVC mount) — the application-layer check, by contrast, works independently of backend type and is the actual enforcement instance; S3 Object Lock is an additional, not the sole, safeguard.
- **`retention_until` on every `ObjectCopy`, not only on locked targets**: simplifies `record_copy`/`replication.py` (one field, always written, regardless of whether the respective target has `object_lock_mode` set) and keeps the door open to switch a target to governance mode later without having to backfill existing copies.
- **Role check exactly like `document-service`'s `kennzeichen_admin_role`** (P5e-S2): no new authorization mechanism, but reuse of the already-established `X-DMS-Roles` header pattern.
- **Governance mode, not compliance mode, is not a dilution of retention protection**: 5.2 requires a regular, enforced retention period — 5.2a *explicitly* requires a sanctioned exception to it (forced deletion with the four-eyes principle). Governance mode with role-bound bypass maps exactly this combination; compliance mode would technically rule out the second requirement.

## Consequences

- `StorageBackend.write()`/`delete()` are no longer minimal interfaces (`lock_until`/`bypass_governance` as new keyword arguments) — a deliberate break accepted within this session, since both parameters are indispensable for WORM and all three implementations (`local_backend.py`, `s3_backend.py`, test doubles) were updated in the same commit.
- `document-service` is the only current caller that passes `retain_until` when writing (from `Document.retention_until`, seedable via `ObjectType.default_retention_days`) — other services that use `storage-service` are unaffected by this extension (the parameter is optional, default `None`).
- The `local` backend type still offers **no** real WORM — only the application-layer check. Anyone needing real tamper protection on local storage must use an S3-compatible target with `object_lock_mode=governance`. Documented as a deliberate, not accidental, gap.
- `replication.py`'s `process_pending` propagates `retention_until` to `record_copy`, but does not (yet) propagate `lock_until` to the actual backend `write()` call during catch-up replication — an existing gap that only becomes relevant once catch-up replication is regularly used for governance targets (retry-queue case, see ADR 0017).
