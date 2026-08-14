from datetime import UTC, datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service.models import (
    BackendIdentity,
    GuardConfig,
    ObjectCopy,
    ObjectMetadata,
    OperationalConfig,
    TargetOverride,
)

_GUARD_CONFIG_ID = 1
_OPERATIONAL_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


async def upsert_metadata(
    session: AsyncSession,
    *,
    object_key: str,
    backend: str,
    checksum_sha256: str,
    size_bytes: int,
    content_type: str | None,
) -> ObjectMetadata:
    now = datetime.now(UTC)
    existing = await session.get(ObjectMetadata, object_key)
    if existing is None:
        existing = ObjectMetadata(object_key=object_key, created_at=now)
        session.add(existing)

    existing.backend = backend
    existing.checksum_sha256 = checksum_sha256
    existing.size_bytes = size_bytes
    existing.content_type = content_type
    existing.updated_at = now

    await session.flush()
    return existing


async def get_metadata(session: AsyncSession, object_key: str) -> ObjectMetadata:
    metadata = await session.get(ObjectMetadata, object_key)
    if metadata is None:
        raise NotFoundError(object_key)
    return metadata


async def delete_metadata(session: AsyncSession, object_key: str) -> None:
    metadata = await session.get(ObjectMetadata, object_key)
    if metadata is None:
        raise NotFoundError(object_key)
    await session.delete(metadata)
    # Without a flush, the object remains findable via the identity-map
    # fast path of `session.get()`, even though it's marked for deletion
    # (the DELETE statement itself is only actually issued here).
    await session.flush()


async def record_copy(
    session: AsyncSession,
    object_key: str,
    backend_id: str,
    *,
    status: str,
    checksum: str | None = None,
    last_error: str | None = None,
    increment_attempt: bool = False,
    retention_until: datetime | None = None,
    next_retry_at: datetime | None = None,
) -> ObjectCopy:
    """Creates or updates a row in ``object_copy`` (3.6) - one call per
    (object_key, backend_id) for each write/replication/fixity attempt.
    ``retention_until`` (5.1/5.2a, since P7-S1) is only applied when a
    value is explicitly set - an already-set value is preserved on calls
    without this argument (fixity checks, error cases). ``next_retry_at``
    (since Post-Roadmap Phase 20 Session 6, ADR 0082), by contrast, is
    UNCONDITIONALLY set like ``last_error`` (default `None`) - a
    successful or fresh write attempt needs no remaining backoff wait
    time, so any previously set value must disappear, not be preserved."""
    now = datetime.now(UTC)
    existing = await session.get(ObjectCopy, (object_key, backend_id))
    if existing is None:
        existing = ObjectCopy(
            object_key=object_key, backend_id=backend_id, attempts=0, created_at=now
        )
        session.add(existing)

    existing.status = status
    existing.last_error = last_error
    if checksum is not None:
        existing.checksum_sha256 = checksum
    if increment_attempt:
        existing.attempts += 1
    if retention_until is not None:
        existing.retention_until = retention_until
    existing.next_retry_at = next_retry_at
    existing.updated_at = now

    await session.flush()
    return existing


async def get_copy(session: AsyncSession, object_key: str, backend_id: str) -> ObjectCopy | None:
    return await session.get(ObjectCopy, (object_key, backend_id))


async def get_any_ok_copy(session: AsyncSession, object_key: str) -> ObjectCopy | None:
    result = await session.execute(
        select(ObjectCopy)
        .where(ObjectCopy.object_key == object_key, ObjectCopy.status == "ok")
        .limit(1)
    )
    return result.scalars().first()


async def list_copies(session: AsyncSession, object_key: str) -> list[ObjectCopy]:
    result = await session.execute(select(ObjectCopy).where(ObjectCopy.object_key == object_key))
    return list(result.scalars().all())


async def list_pending_copies(session: AsyncSession, *, limit: int) -> list[ObjectCopy]:
    """Retry queue (3.6): all copies that have not yet been replicated
    successfully, are not yet considered permanently failed, and (since
    Post-Roadmap Phase 20 Session 6, ADR 0082) whose full-jitter backoff
    has already expired - ``next_retry_at IS NULL`` (new row, never
    failed) always counts as due immediately."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(ObjectCopy)
        .where(
            ObjectCopy.status.in_(["pending", "failed"]),
            or_(ObjectCopy.next_retry_at.is_(None), ObjectCopy.next_retry_at <= now),
        )
        .limit(limit)
    )
    return list(result.scalars().all())


async def delete_copy(session: AsyncSession, object_key: str, backend_id: str) -> None:
    existing = await session.get(ObjectCopy, (object_key, backend_id))
    if existing is not None:
        await session.delete(existing)
        await session.flush()


async def delete_copies_for_key(session: AsyncSession, object_key: str) -> None:
    for copy in await list_copies(session, object_key):
        await session.delete(copy)
    await session.flush()


async def reset_copies_for_backend(session: AsyncSession, backend_id: str) -> int:
    """Resets every existing copy row of a target back to `pending`
    (3.6 "background replication mandatory", P5b-S6) - after a storage
    device swap, every copy previously stored there is considered lost,
    regardless of its last known status. `POST /replication/
    process-pending` then picks them up via the already-existing retry
    queue (no new background task, see ADR 0004/0017). Returns the
    number of reset rows (for logging/audit)."""
    result = await session.execute(
        update(ObjectCopy)
        .where(ObjectCopy.backend_id == backend_id)
        .values(status="pending", attempts=0, last_error=None, updated_at=datetime.now(UTC))
    )
    await session.flush()
    return result.rowcount or 0


async def seed_pending_copies_for_new_target(session: AsyncSession, backend_id: str) -> int:
    """Rebalancing for a newly added target (3.6/7.2, P5c-S2): creates a
    new `pending` row for every already-existing object that does not yet
    have a copy row for `backend_id` - `POST /replication/
    process-pending` picks them up via the already-existing retry queue,
    no new mechanism. Called exclusively during a target's first-start
    bootstrap (see `identity_guard.check_target_identity`), hence no
    separate gate logic here. Returns the number of newly created rows
    (for logging)."""
    already_covered = select(ObjectCopy.object_key).where(ObjectCopy.backend_id == backend_id)
    result = await session.execute(
        select(ObjectMetadata.object_key).where(ObjectMetadata.object_key.notin_(already_covered))
    )
    now = datetime.now(UTC)
    count = 0
    for (object_key,) in result.all():
        session.add(
            ObjectCopy(
                object_key=object_key,
                backend_id=backend_id,
                status="pending",
                attempts=0,
                created_at=now,
                updated_at=now,
            )
        )
        count += 1
    await session.flush()
    return count


async def count_pending_copies_by_backend(session: AsyncSession) -> dict[str, int]:
    """Count of not-yet-successfully-replicated copies per target
    (`pending`/`failed`) - basis for the admin-UI status block (3.6
    "visible as status in the admin UI"), e.g. to show ongoing recovery
    progress after a degraded start."""
    result = await session.execute(
        select(ObjectCopy.backend_id, func.count())
        .where(ObjectCopy.status.in_(["pending", "failed"]))
        .group_by(ObjectCopy.backend_id)
    )
    return dict(result.all())


async def get_storage_usage(session: AsyncSession) -> list[tuple[str, int, int]]:
    """Storage usage per backend (5.4a, since P7-S2b) - `object_metadata.
    backend` is the primary target `id` at the time of the last write
    (see models.py), it says nothing about redundancy copies
    (`ObjectCopy`)."""
    result = await session.execute(
        select(
            ObjectMetadata.backend,
            func.count(),
            func.coalesce(func.sum(ObjectMetadata.size_bytes), 0),
        ).group_by(ObjectMetadata.backend)
    )
    return list(result.all())


async def get_backend_identity(session: AsyncSession, target_id: str) -> BackendIdentity | None:
    return await session.get(BackendIdentity, target_id)


async def list_backend_identities(session: AsyncSession) -> list[BackendIdentity]:
    result = await session.execute(select(BackendIdentity))
    return list(result.scalars().all())


async def record_backend_identity(
    session: AsyncSession, target_id: str, device_id: str
) -> BackendIdentity:
    """Creates the known device ID for a target or re-confirms it
    (`verified_at` is always updated) - a single call covers both the
    first start (creation) and every later successful match check (3.6,
    P5b-S6)."""
    now = datetime.now(UTC)
    identity = await session.get(BackendIdentity, target_id)
    if identity is None:
        identity = BackendIdentity(target_id=target_id, device_id=device_id, verified_at=now)
        session.add(identity)
    else:
        identity.device_id = device_id
        identity.verified_at = now
    await session.flush()
    return identity


async def get_guard_config(session: AsyncSession) -> GuardConfig:
    """Get-or-create with default `allow_degraded_start=False` (same
    pattern as `ocr_service.repository.get_config`, P5b-S5/ADR 0016) - no
    separate migration/seed script needed."""
    config = await session.get(GuardConfig, _GUARD_CONFIG_ID)
    if config is None:
        config = GuardConfig(
            id=_GUARD_CONFIG_ID, allow_degraded_start=False, updated_at=datetime.now(UTC)
        )
        session.add(config)
        await session.flush()
    return config


async def update_guard_config(session: AsyncSession, *, allow_degraded_start: bool) -> GuardConfig:
    config = await get_guard_config(session)
    config.allow_degraded_start = allow_degraded_start
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def get_operational_config(
    session: AsyncSession,
    *,
    default_write_strategy: str,
    default_quorum_count: int,
    default_max_replication_attempts: int,
) -> OperationalConfig:
    """Get-or-create (Post-Roadmap Phase 22 Session 6, ADR 0091), same
    pattern as `get_guard_config`. The defaults deliberately come as
    parameters from the caller (`main.py`, from `Settings`) instead of
    being read directly from `Settings` here - this keeps `repository.py`
    free of any env-var knowledge (this module's convention); only when
    the row is created for the very first time is the previous env-var
    value adopted, so an upgrade to this session does not silently change
    current behavior."""
    config = await session.get(OperationalConfig, _OPERATIONAL_CONFIG_ID)
    if config is None:
        config = OperationalConfig(
            id=_OPERATIONAL_CONFIG_ID,
            write_strategy=default_write_strategy,
            quorum_count=default_quorum_count,
            max_replication_attempts=default_max_replication_attempts,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_operational_config(
    session: AsyncSession,
    *,
    write_strategy: str,
    quorum_count: int,
    max_replication_attempts: int,
) -> OperationalConfig:
    config = await session.get(OperationalConfig, _OPERATIONAL_CONFIG_ID)
    if config is None:
        config = OperationalConfig(id=_OPERATIONAL_CONFIG_ID, updated_at=datetime.now(UTC))
        session.add(config)
    config.write_strategy = write_strategy
    config.quorum_count = quorum_count
    config.max_replication_attempts = max_replication_attempts
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config


async def list_target_overrides(session: AsyncSession) -> list[TargetOverride]:
    """Sparse (Post-Roadmap Phase 22 Session 7, ADR 0092) - only targets
    with an actually set override have a row, see the `TargetOverride`
    docstring. `main.py._compute_target_state()` calls this fresh on
    every relevant write access."""
    result = await session.execute(select(TargetOverride))
    return list(result.scalars().all())


async def upsert_target_override(
    session: AsyncSession, target_id: str, *, object_lock_mode: str | None, role: str | None
) -> TargetOverride:
    override = await session.get(TargetOverride, target_id)
    if override is None:
        override = TargetOverride(target_id=target_id, updated_at=datetime.now(UTC))
        session.add(override)
    override.object_lock_mode = object_lock_mode
    override.role = role
    override.updated_at = datetime.now(UTC)
    await session.flush()
    return override
