from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from ocr_service.models import OcrConfig, OcrResult
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_BATCH_SIZE = 4
_CONFIG_ID = 1


class NotFoundError(Exception):
    pass


def ocr_result_id(document_id: str, version_number: int) -> str:
    return f"{document_id}:{version_number}"


async def upsert_ocr_result(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    status: str,
    engine: str,
    average_confidence: float,
    full_text: str,
    pages: list,
    page_image_storage_key: str | None,
    error_message: str | None,
    attempts: int = 0,
    next_retry_at: datetime | None = None,
) -> OcrResult:
    """Creates an OCR result or overwrites an already existing row with the
    same natural key (see models.py) - makes reprocessing the same version
    idempotent instead of accumulating duplicates. `attempts`/`next_retry_at`
    are reset to the defaults (0/None) on success/skip - a successful rerun
    after previous failures clears their backoff state. Failures go through
    `record_failure` below, which computes the actual values (Post-Roadmap
    Phase 20 Session 4, ADR 0080)."""
    key = ocr_result_id(document_id, version_number)
    now = datetime.now(UTC)
    result = await session.get(OcrResult, key)
    if result is None:
        result = OcrResult(
            id=key, document_id=document_id, version_number=version_number, created_at=now
        )
        session.add(result)
    result.status = status
    result.engine = engine
    result.average_confidence = average_confidence
    result.full_text = full_text
    result.pages = pages
    result.page_image_storage_key = page_image_storage_key
    result.error_message = error_message
    result.attempts = attempts
    result.next_retry_at = next_retry_at
    result.updated_at = now
    await session.flush()
    return result


async def record_failure(
    session: AsyncSession,
    *,
    document_id: str,
    version_number: int,
    engine: str,
    error: str,
    max_attempts: int,
) -> OcrResult:
    """Records a technical failure (Post-Roadmap Phase 20 Session 4, ADR 0080) -
    first reads the existing `attempts` count (if the row already exists),
    increments it, and sets `status`/`next_retry_at` accordingly: below
    `max_attempts`, `status` stays `"failed"` (retryable) with a
    `next_retry_at` set via `compute_backoff_seconds`; at `max_attempts`,
    `status` switches to the true terminal status `failed_permanent`."""
    key = ocr_result_id(document_id, version_number)
    existing = await session.get(OcrResult, key)
    attempts = (existing.attempts if existing is not None else 0) + 1
    if attempts >= max_attempts:
        status = "failed_permanent"
        next_retry_at = None
    else:
        status = "failed"
        delay = compute_backoff_seconds(attempts - 1)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    return await upsert_ocr_result(
        session,
        document_id=document_id,
        version_number=version_number,
        status=status,
        engine=engine,
        average_confidence=0.0,
        full_text="",
        pages=[],
        page_image_storage_key=None,
        error_message=error,
        attempts=attempts,
        next_retry_at=next_retry_at,
    )


async def reset_for_retry(session: AsyncSession, result: OcrResult) -> None:
    """Resets `attempts`/`error_message`/`next_retry_at` before a manual
    restart (Post-Roadmap Phase 20 Session 4, ADR 0080) - MUST run before a
    renewed `process_version` call: otherwise `record_failure` keeps
    counting up from the already exhausted `attempts` number and a
    `failed_permanent` result could never escape this state again (found as
    a genuine bug during live verification of this session -
    `retry_ocr_result` initially called `process_version` without this
    reset)."""
    result.attempts = 0
    result.error_message = None
    result.next_retry_at = None
    await session.flush()


async def list_due_for_retry(session: AsyncSession) -> list[OcrResult]:
    """Retryable OCR results whose backoff window has already expired
    (Post-Roadmap Phase 20 Session 4, ADR 0080) - processed by the new
    `_ocr_retry_poll_loop`."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(OcrResult).where(
            OcrResult.status == "failed",
            or_(OcrResult.next_retry_at.is_(None), OcrResult.next_retry_at <= now),
        )
    )
    return list(result.scalars().all())


async def get_ocr_result(session: AsyncSession, ocr_result_key: str) -> OcrResult:
    result = await session.get(OcrResult, ocr_result_key)
    if result is None:
        raise NotFoundError(f"OCR-Ergebnis {ocr_result_key!r} unbekannt")
    return result


async def list_ocr_results(
    session: AsyncSession,
    *,
    document_id: str | None = None,
    version_number: int | None = None,
    status: str | None = None,
) -> list[OcrResult]:
    """``document_id`` has been optional since Post-Roadmap Phase 20
    Session 7 - without it, this returns a cross-document list (admin UI
    need: see all `failed_permanent` results, not just those of a single
    document)."""
    query = select(OcrResult)
    if document_id is not None:
        query = query.where(OcrResult.document_id == document_id)
    if version_number is not None:
        query = query.where(OcrResult.version_number == version_number)
    if status is not None:
        query = query.where(OcrResult.status == status)
    result = await session.execute(query.order_by(OcrResult.version_number))
    return list(result.scalars().all())


async def get_config(session: AsyncSession) -> OcrConfig:
    """Reads the (single) configuration row, creating it with defaults if it
    has never been saved before (fresh service, before the first `PUT
    /config`) - makes a separate migration/seed script unnecessary."""
    config = await session.get(OcrConfig, _CONFIG_ID)
    if config is None:
        config = OcrConfig(
            id=_CONFIG_ID,
            max_word_count=None,
            batch_size=DEFAULT_BATCH_SIZE,
            # Only PDFs by default (user feedback) - raster images require a
            # deliberate admin opt-in via PUT /config, no automatic OCR run
            # on every uploaded image.
            allowed_content_types=["application/pdf"],
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def update_config(
    session: AsyncSession,
    *,
    max_word_count: int | None,
    batch_size: int,
    allowed_content_types: list[str],
) -> OcrConfig:
    config = await get_config(session)
    config.max_word_count = max_word_count
    config.batch_size = batch_size
    config.allowed_content_types = allowed_content_types
    config.updated_at = datetime.now(UTC)
    await session.flush()
    return config
