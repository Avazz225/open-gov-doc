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
    """Legt eine Ersatzdarstellung/Vorschau an oder überschreibt eine bereits
    vorhandene Zeile mit demselben natürlichen Schlüssel (siehe models.py) -
    macht erneutes Verarbeiten derselben Version idempotent statt Duplikate
    anzuhäufen. `attempts`/`next_retry_at` werden bei Erfolg auf die Defaults
    (0/None) gesetzt - ein erfolgreicher Neudurchlauf nach vorherigen
    Fehlschlägen räumt deren Backoff-Zustand auf. Fehlschläge laufen über
    `record_failure` unten (Post-Roadmap Phase 20 Session 4, ADR 0080)."""
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
    """Zeichnet einen technischen Renderer-Fehlschlag auf (Post-Roadmap
    Phase 20 Session 4, ADR 0080) - liest zuerst die bisherige `attempts`-
    Zahl (falls die Zeile schon existiert), erhöht sie und setzt `status`/
    `next_retry_at` entsprechend: unterhalb `max_attempts` bleibt
    `status="failed"` (retry-fähig) mit einem per `compute_backoff_seconds`
    gesetzten `next_retry_at`, ab `max_attempts` wechselt `status` auf das
    echte Terminalstatus `failed_permanent`."""
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
    """Setzt `attempts`/`error_message`/`next_retry_at` vor einem manuellen
    Neustart zurück (Post-Roadmap Phase 20 Session 4, ADR 0080) - MUSS vor
    einem erneuten `retry_rendition`-Aufruf laufen: sonst zählt
    `record_failure` von der bereits erschöpften `attempts`-Zahl weiter und
    eine `failed_permanent`-Rendition könnte nie wieder aus diesem Zustand
    herauskommen (bei der Live-Verifikation dieser Session als echter Bug in
    ocr-service gefunden und hier vorsorglich gleich mitbehoben)."""
    rendition.attempts = 0
    rendition.error_message = None
    rendition.next_retry_at = None
    await session.flush()


async def list_due_for_retry(session: AsyncSession) -> list[Rendition]:
    """Retry-fähige Renditions, deren Backoff-Fenster bereits abgelaufen ist
    (Post-Roadmap Phase 20 Session 4, ADR 0080) - abgearbeitet vom neuen
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
    """Wie `get_rendition()`, aber ohne `NotFoundError` - für die Prüfung im
    OCR-Nachzieheffekt (consumer.py), ob bereits eine `substitute_text`-
    Rendition existiert (z. B. durch `DocxTextExtractionRenderer` erzeugt),
    ohne Exception-Handling nur für diese einfache Existenzprüfung."""
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
    """``document_id`` ist seit Post-Roadmap Phase 20 Session 7 optional -
    ohne ihn liefert dies eine dokumentübergreifende Liste (Admin-UI-Bedarf:
    alle `failed_permanent`-Renditions sehen, nicht nur die eines einzelnen
    Dokuments)."""
    query = select(Rendition)
    if document_id is not None:
        query = query.where(Rendition.document_id == document_id)
    if version_number is not None:
        query = query.where(Rendition.version_number == version_number)
    if status is not None:
        query = query.where(Rendition.status == status)
    result = await session.execute(query.order_by(Rendition.rendition_type))
    return list(result.scalars().all())
