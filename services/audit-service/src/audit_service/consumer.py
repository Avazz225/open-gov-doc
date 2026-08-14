import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from audit_service import deletion_ledger, repository

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    deletion_ledger_path: Path,
) -> Callable[[bytes], Awaitable[None]]:
    """A handler is shared across all subscribed subjects (see
    ``start_consuming``) - NATS invokes it independently per subject
    subscription, so events from different streams (e.g. "registry.>" and
    "document.>") can arrive concurrently. ``append_event`` reads the
    current chain head before writing the new row - without serialization,
    two concurrent calls could read the same ``prev_hash`` and corrupt the
    chain. A simple in-process lock suffices, since the Audit Service is
    designed as the single writer for its own chain (no horizontal scaling
    of multiple Audit Service instances on the same chain).
    """
    append_lock = asyncio.Lock()

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        async with append_lock, session_factory() as session:
            await repository.append_event(session, event)
            await session.commit()
        # Deletion register ledger (10.4, P11-S4): deliberately AFTER the
        # commit and OUTSIDE the append_lock/DB transaction - a separate
        # file, not part of the hash chain, must not block its
        # serialization.
        deletion_ledger.append_if_force_deletion(event, deletion_ledger_path)

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    deletion_ledger_path: Path,
) -> None:
    """A durable consumer per configured subject - `durable="audit-service"`
    ensures that a restart of the Audit Service resumes where it left off
    (no `deliver_new`: the chain must not have any gaps).

    If a stream does not (yet) exist for a subject - e.g. because the
    respective producer service has never run - it is skipped instead of
    blocking service startup (analogous to permission-service, ADR 0001).
    **Known limitation**: if the stream appears later, it is not
    automatically caught up without a restart of this service.
    """
    handler = make_handler(session_factory, deletion_ledger_path)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="audit-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
