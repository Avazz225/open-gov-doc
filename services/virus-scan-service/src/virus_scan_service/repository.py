from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from virus_scan_service.models import ScanResult


class NotFoundError(Exception):
    pass


async def create_scan_result(
    session: AsyncSession,
    *,
    id: str,
    document_id: str | None,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    checksum_sha256: str,
    status: str,
    threat_name: str | None,
    engine: str,
    quarantine_object_key: str | None,
    created_by: str | None,
) -> ScanResult:
    result = ScanResult(
        id=id,
        document_id=document_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
        status=status,
        threat_name=threat_name,
        engine=engine,
        quarantine_object_key=quarantine_object_key,
        created_by=created_by,
        scanned_at=datetime.now(UTC),
    )
    session.add(result)
    await session.flush()
    return result


async def get_scan_result(session: AsyncSession, scan_id: str) -> ScanResult:
    result = await session.get(ScanResult, scan_id)
    if result is None:
        raise NotFoundError(f"scan_id {scan_id!r} unbekannt")
    return result


async def list_scan_results(
    session: AsyncSession, *, document_id: str | None = None, status: str | None = None
) -> list[ScanResult]:
    query = select(ScanResult)
    if document_id is not None:
        query = query.where(ScanResult.document_id == document_id)
    if status is not None:
        query = query.where(ScanResult.status == status)
    result = await session.execute(query.order_by(ScanResult.scanned_at.desc()))
    return list(result.scalars().all())


async def mark_resolved(
    session: AsyncSession,
    scan_id: str,
    *,
    new_status: str,
    resolved_by: str,
    document_id: str | None = None,
) -> ScanResult:
    """Löst einen Quarantäne-Fall auf (2.5, P15-S2) - `new_status` ist
    "released" (Fehlalarm geklärt, `document_id` auf das neu angelegte
    Dokument gesetzt) oder "purged" (endgültig gelöscht)."""
    result = await get_scan_result(session, scan_id)
    result.status = new_status
    result.resolved_by = resolved_by
    result.resolved_at = datetime.now(UTC)
    if document_id is not None:
        result.document_id = document_id
    await session.flush()
    return result
