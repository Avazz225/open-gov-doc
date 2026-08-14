import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    start_transfer: Callable[..., Awaitable[None]],
    action_type: str,
) -> Callable[[bytes], Awaitable[None]]:
    """Four-eyes return channel (4.3, P6-S4 pattern, see
    `document_service.consumer`): a transfer start previously deferred via
    `ApprovalClient.create_request()` is only actually executed here once
    `permission.approval.approved` arrives for
    `action_type="migration.transfer.start"`."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != action_type:
            return
        action_payload = event.payload.get("payload") or {}
        required = ("source_folder_id", "target_installation_id", "created_by")
        if not all(action_payload.get(key) for key in required):
            logger.warning(
                "permission.approval.approved für %r ohne vollständigen Payload erhalten - "
                "ignoriert: %r",
                action_type,
                action_payload,
            )
            return
        async with session_factory() as session:
            await start_transfer(
                session,
                source_folder_id=action_payload["source_folder_id"],
                target_installation_id=action_payload["target_installation_id"],
                created_by=action_payload["created_by"],
                dry_run=action_payload.get("dry_run", False),
                retention_days=action_payload.get("retention_days"),
            )
            await session.commit()

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subject: str,
    session_factory: async_sessionmaker[AsyncSession],
    start_transfer: Callable[..., Awaitable[None]],
    action_type: str,
) -> None:
    handler = make_handler(session_factory, start_transfer, action_type)
    try:
        await bus.subscribe(subject, handler, durable="migration-service")
    except SubjectNotFoundError:
        logger.warning(
            "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
            "Wird bis zum nächsten Neustart nicht konsumiert.",
            subject,
        )
