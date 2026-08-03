from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service.models import BackendIdentity, GuardConfig, ObjectCopy, ObjectMetadata

_GUARD_CONFIG_ID = 1


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
    # Ohne Flush bleibt das Objekt im Identity-Map-Fast-Path von `session.get()`
    # auffindbar, obwohl es zur Löschung vorgemerkt ist (die DELETE-Anweisung
    # selbst wird erst hier tatsächlich abgesetzt).
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
) -> ObjectCopy:
    """Legt eine Zeile in ``object_copy`` an oder aktualisiert sie (3.6) -
    ein Aufruf pro (object_key, backend_id) je Schreib-/Replikations-/
    Fixity-Versuch. ``retention_until`` (5.1/5.2a, seit P7-S1) wird nur bei
    explizit gesetztem Wert übernommen - ein bereits gesetzter Wert bleibt
    bei Aufrufen ohne dieses Argument (Fixity-Checks, Fehlerfälle) erhalten."""
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
    """Retry-Queue (3.6): alle Kopien, die noch nicht erfolgreich repliziert
    sind und noch nicht als dauerhaft fehlgeschlagen gelten."""
    result = await session.execute(
        select(ObjectCopy).where(ObjectCopy.status.in_(["pending", "failed"])).limit(limit)
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
    """Setzt jede vorhandene Kopie-Zeile eines Ziels auf `pending` zurück
    (3.6 "Hintergrund-Replikation zwingend", P5b-S6) - nach einem
    Datenträger-Wechsel gilt jede zuvor dort abgelegte Kopie als verloren,
    unabhängig vom zuletzt bekannten Status. `POST /replication/
    process-pending` zieht sie danach über die bereits bestehende Retry-Queue
    nach (kein neuer Hintergrundtask, siehe ADR 0004/0017). Gibt die Anzahl
    zurückgesetzter Zeilen zurück (fürs Logging/Audit)."""
    result = await session.execute(
        update(ObjectCopy)
        .where(ObjectCopy.backend_id == backend_id)
        .values(status="pending", attempts=0, last_error=None, updated_at=datetime.now(UTC))
    )
    await session.flush()
    return result.rowcount or 0


async def seed_pending_copies_for_new_target(session: AsyncSession, backend_id: str) -> int:
    """Rebalancing bei neu hinzugefügtem Ziel (3.6/7.2, P5c-S2): legt für
    jedes bereits existierende Objekt, das noch keine Kopie-Zeile für
    `backend_id` hat, eine neue `pending`-Zeile an - `POST /replication/
    process-pending` zieht sie über die bereits bestehende Retry-Queue nach,
    kein neuer Mechanismus. Wird ausschließlich beim Erststart-Bootstrap
    eines Ziels aufgerufen (siehe `identity_guard.check_target_identity`),
    daher hier keine eigene Gate-Logik. Gibt die Anzahl neu angelegter
    Zeilen zurück (fürs Logging)."""
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
    """Anzahl noch nicht erfolgreich replizierter Kopien je Ziel (`pending`/
    `failed`) - Grundlage für den Admin-UI-Statusblock (3.6 "im Admin-UI als
    Status sichtbar"), z. B. um einen laufenden Wiederherstellungs-Fortschritt
    nach einem degradierten Start sichtbar zu machen."""
    result = await session.execute(
        select(ObjectCopy.backend_id, func.count())
        .where(ObjectCopy.status.in_(["pending", "failed"]))
        .group_by(ObjectCopy.backend_id)
    )
    return dict(result.all())


async def get_backend_identity(session: AsyncSession, target_id: str) -> BackendIdentity | None:
    return await session.get(BackendIdentity, target_id)


async def list_backend_identities(session: AsyncSession) -> list[BackendIdentity]:
    result = await session.execute(select(BackendIdentity))
    return list(result.scalars().all())


async def record_backend_identity(
    session: AsyncSession, target_id: str, device_id: str
) -> BackendIdentity:
    """Legt die bekannte Geräte-ID für ein Ziel an oder bestätigt sie erneut
    (`verified_at` wird immer aktualisiert) - ein Aufruf deckt sowohl den
    Erststart (Anlage) als auch jede spätere erfolgreiche Übereinstimmungs-
    prüfung ab (3.6, P5b-S6)."""
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
    """Get-or-create mit Default `allow_degraded_start=False` (gleiches
    Muster wie `ocr_service.repository.get_config`, P5b-S5/ADR 0016) - kein
    separates Migrations-/Seed-Skript nötig."""
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
