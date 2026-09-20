"""P55-S1/ADR 0175: reacts to `folder-service`'s `folder.resource.deleted`
so a teamspace whose root folder was deleted directly via `folder-service`
(bypassing this service's own `DELETE /teamspaces/{id}`, which deliberately
preserves the root folder - see `repository.delete_teamspace`'s docstring)
doesn't stay orphaned forever. Same `make_handler`/`start_consuming` shape
as `case-service`'s own consumer.py."""

import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from teamspace_service import repository
from teamspace_service.clients import PermissionServiceClient

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    permission_client: PermissionServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        root_folder_id = event.subject
        if not root_folder_id:
            return

        async with session_factory() as session:
            teamspace = await repository.get_teamspace_by_root_folder_id(session, root_folder_id)
            if teamspace is None:
                # The common case - this event fires for every folder
                # deletion in the installation, not just teamspace roots.
                return

            members = await repository.list_members(session, teamspace.id)
            for member in members:
                await permission_client.revoke_resource_access(
                    principal_id=member.principal_id, resource_id=root_folder_id
                )
                await permission_client.revoke_manager_access(
                    principal_id=member.principal_id, resource_id=root_folder_id
                )

            teamspace_id = teamspace.id
            await repository.delete_teamspace(session, teamspace_id)
            await session.commit()
            logger.info(
                "teamspace_orphan_cleanup_completed: teamspace_id=%s root_folder_id=%s",
                teamspace_id,
                root_folder_id,
            )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    permission_client: PermissionServiceClient,
) -> None:
    handler = make_handler(session_factory, permission_client)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="teamspace-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream fuer Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum naechsten Neustart nicht konsumiert.",
                subject,
            )
