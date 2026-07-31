import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from case_service import repository
from case_service.document_client import DocumentClient

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentClient,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Uebersetzt `workflow.instance.completed` in den Abschluss-Snapshot
    (2.3) einer Umlaufmappe. case-service setzt beim Start einer Instanz
    `business_key = case_id` (siehe main.py:create_case), daher genuegt ein
    Lookup ueber diesen Wert. Instanzen, die nicht von case-service gestartet
    wurden (kein passender `business_key`) oder deren Umlaufmappe bereits
    abgeschlossen ist, werden ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        business_key = event.payload.get("business_key")
        if not business_key:
            return

        async with session_factory() as session:
            case = await repository.get_case_or_none(session, business_key)
            if case is None or case.status != "open":
                return

            active = await repository.get_active_references(session, case.id)
            snapshots: dict[str, int] = {}
            for reference in active:
                document = await document_client.get(reference.document_id)
                if document is not None:
                    snapshots[reference.document_id] = document["current_version_number"]

            await repository.close_case(session, case, snapshots=snapshots)
            await session.commit()
            await publish_event("case.closed", case.id, {"process_instance_id": event.subject})

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentClient,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, document_client, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="case-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream fuer Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum naechsten Neustart nicht konsumiert.",
                subject,
            )
