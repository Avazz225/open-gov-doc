import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from document_service import repository, retention_actions
from document_service.storage_client import StorageClient

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Executes actions previously deferred under the four-eyes principle
    (4.3, P6-S4) only after approval: force-unlock (P6-S4), forced deletion
    since P7-S1 (5.2a, `document.force_delete`), and since P7-S1c the
    deletion request for regular users (5.2, `document.delete`). Other
    action types (e.g. scope locks) do not belong to this service and are
    ignored."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type == "document.force_delete":
            await _handle_force_delete_approved(
                session_factory, storage, governance_bypass_role, publish_event, event
            )
            return
        if action_type == "document.delete":
            await _handle_delete_approved(session_factory, publish_event, event)
            return
        if action_type == "document.classification.declassify":
            await _handle_declassify_approved(session_factory, publish_event, event)
            return
        if action_type != "document.force_unlock":
            return
        action_payload = event.payload.get("payload") or {}
        document_id = action_payload.get("document_id")
        if not document_id:
            # Foreign/malformed payload (e.g. a request created for testing
            # purposes via /approval-requests with the same action_type but
            # without document_id) - log instead of crashing, otherwise the
            # NATS message remains unacknowledged and gets redelivered
            # endlessly (see dms-eventbus-client._callback).
            logger.warning(
                "permission.approval.approved für document.force_unlock ohne document_id "
                "im payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        async with session_factory() as session:
            try:
                original_lock = await repository.force_release_lock(session, document_id)
            except repository.NotFoundError as exc:
                # P66-S3: previously logged locally and returned with no
                # trace anywhere else - ADR 0022's own Consequences section
                # already named this as a known gap ("no execution feedback
                # channel... a failed force-unlock is only logged locally").
                # Publishing an event at least makes the failure visible
                # system-wide (audit-service records every event
                # unconditionally) - `ApprovalRequest.status` staying
                # "approved" forever with no "executed"/"execution_failed"
                # state is a larger state-machine change ADR 0022 itself
                # deferred, not attempted here.
                logger.warning(
                    "Genehmigter Force-Unlock für document_id=%r konnte nicht ausgeführt "
                    "werden (Sperre inzwischen anderweitig aufgelöst)",
                    document_id,
                )
                await publish_event(
                    "document.force_unlock.failed",
                    document_id,
                    {
                        "approval_request_id": event.payload.get("request_id"),
                        "released_by": action_payload.get("released_by"),
                        "reason": str(exc),
                    },
                    actor="system:document-service",
                )
                return
            await session.commit()
            await publish_event(
                "document.lock.force_released",
                document_id,
                {
                    "original_locked_by": original_lock.locked_by,
                    "released_by": action_payload.get("released_by"),
                    "reason": action_payload.get("reason"),
                },
                actor=action_payload.get("released_by"),
            )

    return handle


async def _handle_force_delete_approved(
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    action_payload = event.payload.get("payload") or {}
    document_id = action_payload.get("document_id")
    if not document_id:
        logger.warning(
            "permission.approval.approved für document.force_delete ohne document_id "
            "im payload erhalten - ignoriert: %r",
            action_payload,
        )
        return

    async with session_factory() as session:
        try:
            await repository.get_document(session, document_id)
        except repository.NotFoundError:
            logger.warning(
                "Genehmigte Zwangslöschung für document_id=%r konnte nicht ausgeführt werden "
                "(Dokument inzwischen bereits anderweitig entfernt)",
                document_id,
            )
            return
        await retention_actions.execute_forced_deletion(
            session,
            storage,
            document_id,
            reason=action_payload.get("reason"),
            triggered_by=action_payload.get("triggered_by"),
            governance_bypass_role=governance_bypass_role,
        )
        await session.commit()
        await publish_event(
            "document.force_deleted",
            document_id,
            {
                "reason": action_payload.get("reason"),
                "triggered_by": action_payload.get("triggered_by"),
            },
            actor=action_payload.get("triggered_by"),
        )


async def _handle_delete_approved(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Deletion request workflow for regular users (5.2, since P7-S1c) -
    executes a regular trash move previously deferred under the four-eyes
    principle, once it has been approved. Identical pattern to the
    `document.force_unlock` branch above, just using
    `repository.delete_document` instead of `force_release_lock`."""
    action_payload = event.payload.get("payload") or {}
    document_id = action_payload.get("document_id")
    if not document_id:
        logger.warning(
            "permission.approval.approved für document.delete ohne document_id "
            "im payload erhalten - ignoriert: %r",
            action_payload,
        )
        return

    async with session_factory() as session:
        deleted_by = action_payload.get("deleted_by")
        try:
            await repository.delete_document(session, document_id, deleted_by=deleted_by)
        except repository.NotFoundError:
            logger.warning(
                "Genehmigter Löschantrag für document_id=%r konnte nicht ausgeführt werden "
                "(Dokument inzwischen bereits anderweitig entfernt)",
                document_id,
            )
            return
        await session.commit()
        await publish_event(
            "document.deleted", document_id, {"deleted_by": deleted_by}, actor=deleted_by
        )


async def _handle_declassify_approved(
    session_factory: async_sessionmaker[AsyncSession],
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Executes an already-approved declassification (14.2, P70-S2, ADR
    0204) - the ONLY place in this service that ever calls
    `repository.declassify_document`, exactly like `auth-service`'s
    `superuser.activate` has no execution path outside its own consumer.
    Identical structural pattern to `_handle_delete_approved` above."""
    action_payload = event.payload.get("payload") or {}
    document_id = action_payload.get("document_id")
    if not document_id:
        logger.warning(
            "permission.approval.approved für document.classification.declassify ohne "
            "document_id im payload erhalten - ignoriert: %r",
            action_payload,
        )
        return

    async with session_factory() as session:
        try:
            await repository.declassify_document(
                session, document_id, target_level=action_payload.get("target_classification_level")
            )
        except repository.NotFoundError:
            logger.warning(
                "Genehmigte Deklassifizierung für document_id=%r konnte nicht ausgeführt werden "
                "(Dokument inzwischen bereits anderweitig entfernt)",
                document_id,
            )
            return
        await session.commit()
        await publish_event(
            "document.classification.changed",
            document_id,
            {
                "classification_level": action_payload.get("target_classification_level"),
                "direction": "lowered",
                "reason": action_payload.get("reason"),
            },
            actor=action_payload.get("changed_by"),
        )


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageClient,
    governance_bypass_role: str,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, storage, governance_bypass_role, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="document-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
