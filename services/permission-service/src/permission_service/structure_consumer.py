import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from permission_service import repository
from permission_service.models import ResourceNode

logger = logging.getLogger(__name__)

"""Provisorischer Struktur-Event-Vertrag (siehe docs/services/permission-service.md):

- "*.resource.created"  payload: {resource_id, parent_id, resource_type}
- "*.resource.moved"    payload: {resource_id, new_parent_id}
- "*.resource.deleted"  payload: {resource_id}

Erwarteter Producer: Folder Service (P3-S3), noch nicht gebaut - Subject-Präfix
und Payload-Form können sich beim tatsächlichen Bau noch ändern.
"""


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[bytes], Awaitable[None]]:
    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        async with session_factory() as session:
            if event.event_type.endswith(".resource.created"):
                resource_id = event.payload["resource_id"]
                if await session.get(ResourceNode, resource_id) is None:
                    session.add(
                        ResourceNode(
                            resource_id=resource_id,
                            parent_id=event.payload.get("parent_id"),
                            resource_type=event.payload.get("resource_type", "folder"),
                        )
                    )
            elif event.event_type.endswith(".resource.moved"):
                node = await session.get(ResourceNode, event.payload["resource_id"])
                if node is not None:
                    node.parent_id = event.payload["new_parent_id"]
            elif event.event_type.endswith(".resource.deleted"):
                node = await session.get(ResourceNode, event.payload["resource_id"])
                if node is not None:
                    await session.delete(node)
            else:
                return

            await repository.invalidate_cache(session)
            await session.commit()

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Abonniert jedes konfigurierte Subject. Existiert (noch) kein Stream dafür
    - z. B. weil der Folder Service noch nie gelaufen ist -, wird das Subject
    übersprungen statt den ganzen Service am Start zu hindern. **Bekannte
    Grenze**: Taucht der Stream später auf, holt dieser Service ihn ohne
    Neustart nicht automatisch nach (kein Retry-Loop implementiert).
    """
    handler = make_handler(session_factory)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="permission-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
