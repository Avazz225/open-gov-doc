from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from storage_service.models import ObjectMetadata


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
