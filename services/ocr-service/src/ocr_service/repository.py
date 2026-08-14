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
    """Legt ein OCR-Ergebnis an oder überschreibt eine bereits vorhandene Zeile
    mit demselben natürlichen Schlüssel (siehe models.py) - macht erneutes
    Verarbeiten derselben Version idempotent statt Duplikate anzuhäufen.
    `attempts`/`next_retry_at` werden bei Erfolg/Skip auf die Defaults (0/None)
    gesetzt - ein erfolgreicher Neudurchlauf nach vorherigen Fehlschlägen
    räumt deren Backoff-Zustand auf. Fehlschläge laufen über `record_failure`
    unten, das die tatsächlichen Werte berechnet (Post-Roadmap Phase 20
    Session 4, ADR 0080)."""
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
    """Zeichnet einen technischen Fehlschlag auf (Post-Roadmap Phase 20
    Session 4, ADR 0080) - liest zuerst die bisherige `attempts`-Zahl (falls
    die Zeile schon existiert), erhöht sie und setzt `status`/`next_retry_at`
    entsprechend: unterhalb `max_attempts` bleibt `status="failed"`
    (retry-fähig) mit einem per `compute_backoff_seconds` gesetzten
    `next_retry_at`, ab `max_attempts` wechselt `status` auf das echte
    Terminalstatus `failed_permanent`."""
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
    """Setzt `attempts`/`error_message`/`next_retry_at` vor einem manuellen
    Neustart zurück (Post-Roadmap Phase 20 Session 4, ADR 0080) - MUSS vor
    einem erneuten `process_version`-Aufruf laufen: sonst zählt
    `record_failure` von der bereits erschöpften `attempts`-Zahl weiter und
    ein `failed_permanent`-Ergebnis könnte nie wieder aus diesem Zustand
    herauskommen (bei der Live-Verifikation dieser Session als echter Bug
    gefunden - `retry_ocr_result` rief `process_version` zunächst ohne
    diesen Reset auf)."""
    result.attempts = 0
    result.error_message = None
    result.next_retry_at = None
    await session.flush()


async def list_due_for_retry(session: AsyncSession) -> list[OcrResult]:
    """Retry-fähige OCR-Ergebnisse, deren Backoff-Fenster bereits abgelaufen
    ist (Post-Roadmap Phase 20 Session 4, ADR 0080) - abgearbeitet vom neuen
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
    """``document_id`` ist seit Post-Roadmap Phase 20 Session 7 optional -
    ohne ihn liefert dies eine dokumentübergreifende Liste (Admin-UI-Bedarf:
    alle `failed_permanent`-Ergebnisse sehen, nicht nur die eines einzelnen
    Dokuments)."""
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
    """Liest die (einzige) Konfigurationszeile, legt sie mit Defaults an, falls
    sie noch nie gespeichert wurde (frischer Service, vor dem ersten `PUT
    /config`) - macht ein separates Migrations-/Seed-Skript überflüssig."""
    config = await session.get(OcrConfig, _CONFIG_ID)
    if config is None:
        config = OcrConfig(
            id=_CONFIG_ID,
            max_word_count=None,
            batch_size=DEFAULT_BATCH_SIZE,
            # Standardmäßig nur PDFs (Nutzer-Feedback) - Rasterbilder erfordern
            # eine bewusste Admin-Freigabe über PUT /config, kein automatischer
            # OCR-Lauf auf jedes hochgeladene Bild.
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
