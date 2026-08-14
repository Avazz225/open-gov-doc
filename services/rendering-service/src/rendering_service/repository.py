from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from rendering_service.models import Rendition


class NotFoundError(Exception):
    pass


def rendition_id(document_id: str, version_number: int, rendition_type: str) -> str:
    return f"{document_id}:{version_number}:{rendition_type}"


async def upsert_rendition(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    rendition_type: str,
    source_filename: str,
    source_content_type: str | None,
    target_filename: str,
    target_content_type: str,
    size_bytes: int,
    storage_object_key: str,
    status: str,
    error_message: str | None,
    attempts: int = 0,
    next_retry_at: datetime | None = None,
) -> Rendition:
    """Creates a rendition/preview or overwrites an already existing row with
    the same natural key (see models.py) - makes reprocessing the same
    version idempotent instead of accumulating duplicates. `attempts`/
    `next_retry_at` are reset to the defaults (0/None) on success - a
    successful rerun after previous failures clears their backoff state.
    Failures go through `record_failure` below (Post-Roadmap Phase 20
    Session 4, ADR 0080)."""
    key = rendition_id(document_id, version_number, rendition_type)
    now = datetime.now(UTC)
    rendition = await session.get(Rendition, key)
    if rendition is None:
        rendition = Rendition(
            id=key,
            document_id=document_id,
            version_number=version_number,
            rendition_type=rendition_type,
            created_at=now,
        )
        session.add(rendition)
    rendition.source_filename = source_filename
    rendition.source_content_type = source_content_type
    rendition.target_filename = target_filename
    rendition.target_content_type = target_content_type
    rendition.size_bytes = size_bytes
    rendition.storage_object_key = storage_object_key
    rendition.status = status
    rendition.error_message = error_message
    rendition.attempts = attempts
    rendition.next_retry_at = next_retry_at
    rendition.updated_at = now
    await session.flush()
    return rendition


async def record_failure(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    rendition_type: str,
    source_filename: str,
    source_content_type: str | None,
    error: str,
    max_attempts: int,
) -> Rendition:
    """Records a technical renderer failure (Post-Roadmap Phase 20 Session 4,
    ADR 0080) - first reads the existing `attempts` count (if the row
    already exists), increments it and sets `status`/`next_retry_at`
    accordingly: below `max_attempts` it stays `status="failed"`
    (retryable) with a `next_retry_at` set via `compute_backoff_seconds`,
    from `max_attempts` onward `status` switches to the true terminal
    status `failed_permanent`."""
    key = rendition_id(document_id, version_number, rendition_type)
    existing = await session.get(Rendition, key)
    attempts = (existing.attempts if existing is not None else 0) + 1
    if attempts >= max_attempts:
        status = "failed_permanent"
        next_retry_at = None
    else:
        status = "failed"
        delay = compute_backoff_seconds(attempts - 1)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    return await upsert_rendition(
        session,
        document_id=document_id,
        version_number=version_number,
        rendition_type=rendition_type,
        source_filename=source_filename,
        source_content_type=source_content_type,
        target_filename="",
        target_content_type="",
        size_bytes=0,
        storage_object_key="",
        status=status,
        error_message=error,
        attempts=attempts,
        next_retry_at=next_retry_at,
    )


async def reset_for_retry(session: AsyncSession, rendition: Rendition) -> None:
    """Resets `attempts`/`error_message`/`next_retry_at` before a manual
    restart (Post-Roadmap Phase 20 Session 4, ADR 0080) - MUST run before
    another `retry_rendition` call: otherwise `record_failure` keeps
    counting up from the already exhausted `attempts` value and a
    `failed_permanent` rendition could never leave that state again (found
    as a real bug in ocr-service during this session's live verification
    and fixed here as a precaution at the same time)."""
    rendition.attempts = 0
    rendition.error_message = None
    rendition.next_retry_at = None
    await session.flush()


async def list_due_for_retry(session: AsyncSession) -> list[Rendition]:
    """Retryable renditions whose backoff window has already expired
    (Post-Roadmap Phase 20 Session 4, ADR 0080) - processed by the new
    `_rendition_retry_poll_loop`."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Rendition).where(
            Rendition.status == "failed",
            or_(Rendition.next_retry_at.is_(None), Rendition.next_retry_at <= now),
        )
    )
    return list(result.scalars().all())


async def get_rendition_optional(session: AsyncSession, rendition_key: str) -> Rendition | None:
    """Like `get_rendition()`, but without `NotFoundError` - for the check in
    the OCR follow-up effect (consumer.py) of whether a `substitute_text`
    rendition already exists (e.g. produced by `DocxTextExtractionRenderer`),
    without exception handling just for this simple existence check."""
    return await session.get(Rendition, rendition_key)


async def get_rendition(session: AsyncSession, rendition_key: str) -> Rendition:
    rendition = await session.get(Rendition, rendition_key)
    if rendition is None:
        raise NotFoundError(f"Ersatzdarstellung {rendition_key!r} unbekannt")
    return rendition


async def list_renditions(
    session: AsyncSession,
    *,
    document_id: str | None = None,
    version_number: int | None = None,
    status: str | None = None,
) -> list[Rendition]:
    """``document_id`` has been optional since Post-Roadmap Phase 20
    Session 7 - without it this returns a cross-document list (admin UI
    need: see all `failed_permanent` renditions, not just those of a single
    document)."""
    query = select(Rendition)
    if document_id is not None:
        query = query.where(Rendition.document_id == document_id)
    if version_number is not None:
        query = query.where(Rendition.version_number == version_number)
    if status is not None:
        query = query.where(Rendition.status == status)
    result = await session.execute(query.order_by(Rendition.rendition_type))
    return list(result.scalars().all())
