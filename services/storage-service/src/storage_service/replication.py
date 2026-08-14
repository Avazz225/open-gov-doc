import asyncio
import contextlib
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import repository
from storage_service.backends.interface import ObjectNotFoundError, StorageBackend

logger = logging.getLogger(__name__)


class PrimaryWriteError(Exception):
    """The primary target itself rejected the write attempt - a hard
    failure regardless of the write strategy (no fallback)."""


class QuorumNotReachedError(Exception):
    """With write strategy 'quorum', the configured minimum number of
    targets did not confirm successfully (3.6)."""

    def __init__(self, successes: int, required: int) -> None:
        self.successes = successes
        self.required = required
        super().__init__(f"Quorum nicht erreicht: {successes} von {required} Zielen erfolgreich")


async def write_with_redundancy(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    targets: list[str],
    strategy: str,
    quorum_count: int,
    key: str,
    data: bytes,
    checksum: str,
    retention_until: datetime | None = None,
    lock_target_ids: set[str] | None = None,
) -> dict[str, str]:
    """Writes ``data`` to the configured targets according to the write
    strategy and maintains an ``object_copy`` row per target. Returns
    ``{backend_id: status}`` or raises ``PrimaryWriteError``/
    ``QuorumNotReachedError``.

    ``retention_until`` (5.1/5.2a, since P7-S1) is recorded on EVERY
    ``object_copy`` row (basis for `retention_guard.py`, backend-
    independent) - however, real S3 Object Lock on the write itself is
    only applied to a target from ``lock_target_ids`` (the configured
    `object_lock_mode` targets)."""
    lock_target_ids = lock_target_ids or set()
    if strategy == "quorum":
        results = await asyncio.gather(
            *(
                backends[target].write(
                    key, data, lock_until=retention_until if target in lock_target_ids else None
                )
                for target in targets
            ),
            return_exceptions=True,
        )
        statuses: dict[str, str] = {}
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, Exception):
                statuses[target] = "failed"
                await repository.record_copy(
                    session, key, target, status="failed", last_error=str(result)
                )
            else:
                statuses[target] = "ok"
                await repository.record_copy(
                    session,
                    key,
                    target,
                    status="ok",
                    checksum=checksum,
                    retention_until=retention_until,
                )

        successes = sum(1 for status in statuses.values() if status == "ok")
        if successes < quorum_count:
            for target, status in statuses.items():
                if status == "ok":
                    with contextlib.suppress(ObjectNotFoundError, Exception):
                        await backends[target].delete(key)
                    await repository.delete_copy(session, key, target)
            raise QuorumNotReachedError(successes, quorum_count)
        return statuses

    # "primary_async": primary target synchronous, secondary targets stay
    # "pending" and are only caught up via process_pending() (no in-
    # process background task - see ADR 0004).
    primary = targets[0]
    try:
        await backends[primary].write(
            key, data, lock_until=retention_until if primary in lock_target_ids else None
        )
    except Exception as exc:
        raise PrimaryWriteError(str(exc)) from exc

    statuses = {primary: "ok"}
    await repository.record_copy(
        session, key, primary, status="ok", checksum=checksum, retention_until=retention_until
    )
    for secondary in targets[1:]:
        statuses[secondary] = "pending"
        await repository.record_copy(
            session, key, secondary, status="pending", retention_until=retention_until
        )
    return statuses


async def read_with_fallback(
    session: AsyncSession, *, backends: dict[str, StorageBackend], targets: list[str], key: str
) -> bytes:
    """Reads from the first copy with status 'ok' in target priority order
    (primary target first) - automatic fallback on unavailability (3.6)."""
    last_error: Exception | None = None
    for target in targets:
        copy = await repository.get_copy(session, key, target)
        if copy is None or copy.status != "ok":
            continue
        try:
            return await backends[target].read(key)
        except ObjectNotFoundError as exc:
            last_error = exc
            continue
    raise ObjectNotFoundError(key) from last_error


async def write_to_targets(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    targets: list[str],
    key: str,
    data: bytes,
    checksum: str,
) -> dict[str, str]:
    """Writes synchronously to ALL specified targets (5.6, since P7-S3) -
    unlike `write_with_redundancy`, there is no primary/secondary
    distinction and no write-strategy/quorum semantics, since archive
    writes are deliberately synchronous, individual operations (not part
    of the upload hot path). A failing target propagates the backend's
    exception unchanged - all specified targets must succeed."""
    statuses: dict[str, str] = {}
    for target in targets:
        await backends[target].write(key, data)
        statuses[target] = "ok"
        await repository.record_copy(session, key, target, status="ok", checksum=checksum)
    return statuses


async def delete_from_targets(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    targets: list[str],
    key: str,
    bypass_governance: bool = False,
) -> None:
    """Removes copies ONLY from the specified targets (5.6, since P7-S3,
    "dehydrating") - unlike `delete_from_all`, only the `object_copy`
    rows of the named targets are removed, copies on other targets (e.g.
    archive targets) remain untouched."""
    for target in targets:
        with contextlib.suppress(ObjectNotFoundError):
            await backends[target].delete(key, bypass_governance=bypass_governance)
        await repository.delete_copy(session, key, target)


async def delete_from_all(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    targets: list[str],
    key: str,
    bypass_governance: bool = False,
) -> None:
    """``bypass_governance`` (5.1/5.2a) is passed through to every backend -
    the actual authorization check (role, active lock) already happens
    before this call, in `retention_guard.py`/`main.py`."""
    for target in targets:
        with contextlib.suppress(ObjectNotFoundError):
            await backends[target].delete(key, bypass_governance=bypass_governance)
    await repository.delete_copies_for_key(session, key)


async def verify_all_copies(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    key: str,
    expected_checksum: str,
) -> list[dict]:
    """Fixity check across all known copies of an object (3.6): read the
    checksum fresh per backend, compare it against the reference value
    from the shared DB, write the result back to ``object_copy``."""
    results = []
    for copy in await repository.list_copies(session, key):
        if copy.status == "pending":
            results.append({"backend_id": copy.backend_id, "status": "pending", "ok": None})
            continue
        try:
            actual = await backends[copy.backend_id].checksum(key)
        except ObjectNotFoundError:
            await repository.record_copy(
                session, key, copy.backend_id, status="failed", last_error="Objekt im Backend fehlt"
            )
            results.append({"backend_id": copy.backend_id, "status": "missing", "ok": False})
            continue

        ok = actual == expected_checksum
        await repository.record_copy(
            session,
            key,
            copy.backend_id,
            status="ok" if ok else "failed",
            checksum=actual,
            last_error=None if ok else "Prüfsumme weicht vom Referenzwert ab",
        )
        results.append(
            {
                "backend_id": copy.backend_id,
                "status": "ok" if ok else "mismatch",
                "ok": ok,
                "expected": expected_checksum,
                "actual": actual,
            }
        )
    return results


def _next_retry_at(attempts: int) -> datetime:
    """Full-jitter backoff (`libs/dms-retry`, since Post-Roadmap Phase 20
    Session 6, ADR 0082) - same formula as in the other four resilience
    spots in this phase. ``attempts`` is already the NEW, incremented
    count (1-indexed), `compute_backoff_seconds` expects a 0-indexed
    attempt counter."""
    delay = compute_backoff_seconds(attempts - 1)
    return datetime.now(UTC) + timedelta(seconds=delay)


async def process_pending(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    max_attempts: int,
    limit: int = 100,
) -> dict:
    """Retry queue for asynchronously caught-up copies (3.6). Reads the
    bytes from an already-confirmed copy of the same object and writes
    them to the pending target. After ``max_attempts`` unsuccessful
    attempts, a copy is considered permanently failed (logged instead of
    alerted, see Settings.max_replication_attempts)."""
    processed = succeeded = failed = permanently_failed = 0
    for copy in await repository.list_pending_copies(session, limit=limit):
        processed += 1
        source = await repository.get_any_ok_copy(session, copy.object_key)
        if source is None:
            failed += 1
            new_attempts = copy.attempts + 1
            await repository.record_copy(
                session,
                copy.object_key,
                copy.backend_id,
                status="failed",
                last_error="keine bestätigte Quellkopie zum Replizieren gefunden",
                increment_attempt=True,
                next_retry_at=_next_retry_at(new_attempts),
            )
            continue

        try:
            data = await backends[source.backend_id].read(copy.object_key)
            # `lock_until` is deliberately NOT passed through to the
            # backend here (open point, see docs/services/
            # storage-service.md): a target that is only populated via
            # re-replication after the original write would otherwise get
            # no real S3 Object Lock - the application-layer guard
            # (`retention_until` below) still applies regardless.
            await backends[copy.backend_id].write(copy.object_key, data)
        except Exception as exc:
            new_attempts = copy.attempts + 1
            if new_attempts >= max_attempts:
                permanently_failed += 1
                status = "failed_permanent"
                next_retry_at = None
                logger.error(
                    "Replikation dauerhaft fehlgeschlagen: object_key=%s backend_id=%s "
                    "attempts=%s error=%s",
                    copy.object_key,
                    copy.backend_id,
                    new_attempts,
                    exc,
                )
            else:
                failed += 1
                status = "failed"
                next_retry_at = _next_retry_at(new_attempts)
            await repository.record_copy(
                session,
                copy.object_key,
                copy.backend_id,
                status=status,
                last_error=str(exc),
                increment_attempt=True,
                next_retry_at=next_retry_at,
            )
            continue

        succeeded += 1
        await repository.record_copy(
            session,
            copy.object_key,
            copy.backend_id,
            status="ok",
            checksum=hashlib.sha256(data).hexdigest(),
            increment_attempt=True,
            retention_until=source.retention_until,
        )

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "permanently_failed": permanently_failed,
    }
