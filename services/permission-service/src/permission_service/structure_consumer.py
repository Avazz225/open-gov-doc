import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from permission_service import repository
from permission_service.models import ResourceNode

logger = logging.getLogger(__name__)

"""Provisional structure event contract (see docs/services/permission-service.md):

- "*.resource.created"  payload: {resource_id, parent_id, resource_type}
- "*.resource.moved"    payload: {resource_id, new_parent_id}
- "*.resource.deleted"  payload: {resource_id}

Expected producer: Folder Service (P3-S3), not yet built - the subject
prefix and payload shape may still change once it is actually built.
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
    """Subscribes to every configured subject. If no stream exists (yet) for
    one - e.g. because the Folder Service has never run - the subject is
    skipped instead of preventing the entire service from starting. **Known
    limitation**: if the stream appears later, this service does not pick
    it up automatically without a restart (no retry loop implemented).
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
