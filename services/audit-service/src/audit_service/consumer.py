import asyncio
import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit_service import repository

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[bytes], Awaitable[None]]:
    """Ein Handler wird über alle abonnierten Subjects hinweg geteilt (siehe
    ``start_consuming``) - NATS ruft je Subject-Subscription unabhängig auf,
    Events aus verschiedenen Streams (z. B. "registry.>" und "document.>")
    können also nebenläufig eintreffen. ``append_event`` liest den aktuellen
    Kettenkopf, bevor es die neue Zeile schreibt - ohne Serialisierung könnten
    zwei nebenläufige Aufrufe denselben ``prev_hash`` lesen und die Kette
    korrumpieren. Ein einfacher In-Prozess-Lock genügt, da der Audit Service
    als Single-Writer für seine eigene Kette konzipiert ist (keine horizontale
    Skalierung mehrerer Audit-Service-Instanzen auf derselben Kette).
    """
    append_lock = asyncio.Lock()

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        async with append_lock, session_factory() as session:
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

    Existiert für ein Subject (noch) kein Stream - z. B. weil der jeweilige
    Producer-Service noch nie gelaufen ist -, wird es übersprungen statt den
    Service-Start zu blockieren (analog zu permission-service, ADR 0001).
    **Bekannte Grenze**: taucht der Stream später auf, wird er ohne Neustart
    dieses Service nicht automatisch nachgeholt.
    """
    handler = make_handler(session_factory)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="audit-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
