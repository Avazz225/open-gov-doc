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
    session: AsyncSession, *, document_id: str | None = None
) -> list[ScanResult]:
    query = select(ScanResult)
    if document_id is not None:
        query = query.where(ScanResult.document_id == document_id)
    result = await session.execute(query.order_by(ScanResult.scanned_at.desc()))
    return list(result.scalars().all())
