from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit_service import repository


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[bytes], Awaitable[None]]:
    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        async with session_factory() as session:
            await repository.append_event(session, event)
            await session.commit()

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ein durable Consumer je konfiguriertem Subject - `durable="audit-service"`
    sorgt dafür, dass ein Neustart des Audit Service dort weitermacht, wo er
    aufgehört hat (kein `deliver_new`: die Kette darf keine Lücke haben).
    """
    handler = make_handler(session_factory)
    for subject in subjects:
        await bus.subscribe(subject, handler, durable="audit-service")
