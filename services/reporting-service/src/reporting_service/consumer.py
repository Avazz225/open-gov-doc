import logging

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from reporting_service import repository

logger = logging.getLogger(__name__)


def make_handler(session_factory: async_sessionmaker[AsyncSession]):
    """Speist das Dokumentenaufkommen-Read-Modell (5.4a) - nur
    `document.created` ist relevant, jeder andere `document.*`-Event wird
    ignoriert (kein Interesse an Loeschung/Wiederherstellung/Metadaten, s.
    models.py `DocumentCreatedEvent`-Docstring)."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.event_type != "document.created":
            return
        document_id = event.subject
        if not document_id:
            logger.warning("document.created ohne subject erhalten - ignoriert: %r", event.payload)
            return
        async with session_factory() as session:
            await repository.record_document_created(
                session,
                document_id=document_id,
                folder_id=event.payload.get("folder_id"),
                occurred_at=event.occurred_at,
            )
            await session.commit()

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    handler = make_handler(session_factory)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="reporting-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream fuer Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum naechsten Neustart nicht konsumiert.",
                subject,
            )
