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
    """Das Primärziel selbst hat den Schreibversuch abgelehnt - unabhängig
    von der Schreibstrategie ein harter Fehlschlag (kein Fallback)."""


class QuorumNotReachedError(Exception):
    """Bei Schreibstrategie 'quorum' hat nicht die konfigurierte
    Mindestanzahl an Zielen erfolgreich bestätigt (3.6)."""

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
    """Schreibt ``data`` gemäß Schreibstrategie auf die konfigurierten Ziele
    und pflegt je Ziel eine ``object_copy``-Zeile. Gibt ``{backend_id: status}``
    zurück oder wirft ``PrimaryWriteError``/``QuorumNotReachedError``.

    ``retention_until`` (5.1/5.2a, seit P7-S1) wird auf JEDER ``object_copy``-
    Zeile vermerkt (Grundlage für `retention_guard.py`, backend-unabhängig) -
    echtes S3 Object Lock beim Schreiben selbst bekommt aber nur ein Ziel aus
    ``lock_target_ids`` (die konfigurierten `object_lock_mode`-Ziele)."""
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

    # "primary_async": Primärziel synchron, Sekundärziele bleiben "pending"
    # und werden erst über process_pending() nachgezogen (kein In-Prozess-
    # Hintergrundtask - siehe ADR 0004).
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
    """Liest von der ersten Kopie mit Status 'ok' in Zielpriorität
    (Primärziel zuerst) - automatischer Fallback bei Nichterreichbarkeit (3.6)."""
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
    """Schreibt synchron auf ALLE angegebenen Ziele (5.6, seit P7-S3) - anders
    als `write_with_redundancy` kein Primär-/Sekundär-Unterschied und keine
    Schreibstrategie/Quorum-Semantik, da Archiv-Schreibvorgänge bewusst
    synchrone Einzelvorgänge sind (kein Teil des Upload-Hot-Path). Ein
    fehlschlagendes Ziel wirft die Exception des Backends unverändert weiter -
    alle angegebenen Ziele müssen erfolgreich sein."""
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
    """Entfernt Kopien NUR von den angegebenen Zielen (5.6, seit P7-S3,
    "Dehydrieren") - anders als `delete_from_all` werden nur die
    `object_copy`-Zeilen der genannten Ziele entfernt, Kopien auf anderen
    Zielen (z. B. Archiv-Zielen) bleiben unberührt."""
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
    """``bypass_governance`` (5.1/5.2a) wird an jedes Backend durchgereicht -
    die eigentliche Autorisierungsprüfung (Rolle, aktive Sperre) liegt bereits
    vor diesem Aufruf in `retention_guard.py`/`main.py`."""
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
    """Fixity-Check über alle bekannten Kopien eines Objekts (3.6): Prüfsumme
    je Backend neu lesen, mit dem Referenzwert aus der Shared DB abgleichen,
    Ergebnis in ``object_copy`` zurückschreiben."""
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
    """Full-Jitter-Backoff (`libs/dms-retry`, seit Post-Roadmap Phase 20
    Session 6, ADR 0082) - gleiche Formel wie an den vier anderen
    Resilienz-Stellen dieser Phase. ``attempts`` ist bereits die NEUE,
    inkrementierte Zählung (1-indiziert), `compute_backoff_seconds` erwartet
    einen 0-indizierten Versuchszähler."""
    delay = compute_backoff_seconds(attempts - 1)
    return datetime.now(UTC) + timedelta(seconds=delay)


async def process_pending(
    session: AsyncSession,
    *,
    backends: dict[str, StorageBackend],
    max_attempts: int,
    limit: int = 100,
) -> dict:
    """Retry-Queue für asynchron nachzuziehende Kopien (3.6). Liest die Bytes
    von einer bereits bestätigten Kopie desselben Objekts und schreibt sie
    aufs ausstehende Ziel. Nach ``max_attempts`` erfolglosen Versuchen gilt
    eine Kopie als dauerhaft fehlgeschlagen (Log statt Alarmierung, siehe
    Settings.max_replication_attempts)."""
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
            # `lock_until` wird hier bewusst NICHT an das Backend
            # durchgereicht (offener Punkt, siehe docs/services/
            # storage-service.md): ein Ziel, das erst nach dem ursprünglichen
            # Schreibvorgang per Nachreplikation befüllt wird, bekäme sonst
            # kein echtes S3 Object Lock - der Anwendungsschicht-Guard
            # (`retention_until` unten) greift trotzdem unabhängig davon.
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
