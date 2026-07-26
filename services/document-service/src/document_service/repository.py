from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from document_service.models import Document, DocumentLock, DocumentVersion


class NotFoundError(Exception):
    pass


class LockConflictError(Exception):
    """Ein anderer Nutzer hält aktuell die Bearbeitungssperre (4.2)."""


class LockNotHeldError(Exception):
    """Regulärer Unlock-Versuch durch jemanden, der die Sperre nicht hält."""


async def get_document(session: AsyncSession, document_id: str) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise NotFoundError(f"document_id {document_id!r} unbekannt")
    return document


async def create_document(
    session: AsyncSession,
    *,
    document_id: str,
    title: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    storage_object_key: str,
    folder_id: str | None,
    object_type_id: str | None,
    created_by: str,
) -> Document:
    now = datetime.now(UTC)
    document = Document(
        id=document_id,
        title=title,
        folder_id=folder_id,
        object_type_id=object_type_id,
        current_version_number=1,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(document)
    session.add(
        DocumentVersion(
            document_id=document_id,
            version_number=1,
            storage_object_key=storage_object_key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            is_conflict=False,
            based_on_version_number=None,
            created_by=created_by,
            created_at=now,
        )
    )
    await session.flush()
    return document


async def delete_document(session: AsyncSession, document_id: str, *, deleted_by: str) -> Document:
    """Weiche Löschung (Sichtbarkeit aus, Metadaten bleiben). Aufbewahrung/
    Zwangslöschung/Löschregister (5.2/5.2a) sind bewusst nicht Teil dieser
    Session - folgen mit dem Compliance-Service (Phase 7)."""
    document = await get_document(session, document_id)
    document.deleted_at = datetime.now(UTC)
    document.updated_at = document.deleted_at
    await session.flush()
    return document


async def list_versions(session: AsyncSession, document_id: str) -> list[DocumentVersion]:
    await get_document(session, document_id)
    result = await session.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
    )
    return list(result.scalars().all())


async def get_version(
    session: AsyncSession, document_id: str, version_number: int
) -> DocumentVersion:
    result = await session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:
        raise NotFoundError(f"Version {version_number} von {document_id!r} unbekannt")
    return version


async def get_current_version(session: AsyncSession, document_id: str) -> DocumentVersion:
    document = await get_document(session, document_id)
    return await get_version(session, document_id, document.current_version_number)


async def get_lock(session: AsyncSession, document_id: str) -> DocumentLock | None:
    return await session.get(DocumentLock, document_id)


def _is_active(lock: DocumentLock, now: datetime) -> bool:
    return lock.expires_at > now


async def acquire_lock(
    session: AsyncSession,
    document_id: str,
    *,
    locked_by: str,
    session_id: str,
    timeout_seconds: float,
) -> DocumentLock:
    document = await get_document(session, document_id)
    now = datetime.now(UTC)
    lock = await session.get(DocumentLock, document_id)

    if lock is not None and _is_active(lock, now) and lock.locked_by != locked_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    if lock is None:
        lock = DocumentLock(document_id=document_id)
        session.add(lock)

    lock.locked_by = locked_by
    lock.session_id = session_id
    lock.based_on_version_number = document.current_version_number
    lock.locked_at = now
    lock.expires_at = now + timedelta(seconds=timeout_seconds)
    await session.flush()
    return lock


async def release_lock(session: AsyncSession, document_id: str, *, released_by: str) -> None:
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        return  # bereits frei - idempotent, kein Fehler
    if lock.locked_by != released_by:
        raise LockNotHeldError(
            f"Sperre an {document_id!r} wird von {lock.locked_by!r} gehalten, "
            f"nicht von {released_by!r}"
        )
    await session.delete(lock)
    await session.flush()


async def force_release_lock(session: AsyncSession, document_id: str) -> DocumentLock:
    """Administrativer Force-Unlock (4.2). Gibt die zuvor aktive Sperre
    zurück, damit der Aufrufer den ursprünglichen Halter für Benachrichtigung/
    Audit kennt. Die eigentliche Konfliktkopie-Absicherung entsteht nicht hier,
    sondern optimistisch beim nächsten Check-in (siehe checkin_version) -
    siehe ADR 0002 für die Begründung dieser Vereinfachung."""
    lock = await session.get(DocumentLock, document_id)
    if lock is None:
        raise NotFoundError(f"Dokument {document_id!r} ist nicht gesperrt")
    await session.delete(lock)
    await session.flush()
    return lock


def _conflict_filename(filename: str, *, created_by: str, now: datetime) -> str:
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    stem, sep, ext = filename.rpartition(".")
    if sep:
        return f"{stem}_conflict_{created_by}_{timestamp}.{ext}"
    return f"{filename}_conflict_{created_by}_{timestamp}"


async def checkin_version(
    session: AsyncSession,
    document_id: str,
    *,
    expected_base_version_number: int,
    storage_object_key: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    created_by: str,
    comment: str | None = None,
) -> tuple[DocumentVersion, bool]:
    """Optimistische Konflikterkennung (4.2, konkretisiert): stimmt die vom
    Client angegebene Ausgangsversion nicht mit der aktuellen Hauptversion
    überein (z. B. weil in der Zwischenzeit ein Force-Unlock + regulärer
    Check-in eines anderen Nutzers stattfand), wird der Upload NICHT
    überschreibend eingespielt, sondern als eigenständige, weiterhin
    abrufbare Konfliktkopie neben der aktuellen Version abgelegt - der
    Hauptversions-Zeiger bewegt sich in diesem Fall nicht.

    Gibt ``(version, is_conflict)`` zurück.
    """
    document = await get_document(session, document_id)
    lock = await session.get(DocumentLock, document_id)
    now = datetime.now(UTC)

    if lock is not None and _is_active(lock, now) and lock.locked_by != created_by:
        raise LockConflictError(f"Dokument {document_id!r} ist gesperrt von {lock.locked_by!r}")

    is_conflict = expected_base_version_number != document.current_version_number

    max_version = await session.execute(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    next_version_number = (max_version.scalar_one() or 0) + 1

    final_filename = (
        _conflict_filename(filename, created_by=created_by, now=now) if is_conflict else filename
    )

    version = DocumentVersion(
        document_id=document_id,
        version_number=next_version_number,
        storage_object_key=storage_object_key,
        filename=final_filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        is_conflict=is_conflict,
        based_on_version_number=expected_base_version_number,
        comment=comment,
        created_by=created_by,
        created_at=now,
    )
    session.add(version)

    if not is_conflict:
        document.current_version_number = next_version_number
        document.updated_at = now

    # Check-in beendet regulär die eigene Bearbeitung (4.2) - auch im
    # Konfliktfall, da die Ausgangsbasis ohnehin veraltet war und ein
    # erneuter Versuch ebenfalls über die Konflikterkennung liefe.
    if lock is not None and lock.locked_by == created_by:
        await session.delete(lock)

    await session.flush()
    return version, is_conflict
