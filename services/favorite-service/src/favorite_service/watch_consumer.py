"""Watch matching (P90-S4, ADR 0256): consumes document-/folder-service
events, finds the watches they concern, and publishes one
`favorite.watch.triggered` per watcher. notification-service delivers.

- The person who made the change isn't told about it.
- Before telling anyone, their read permission on the watched object is
  checked again (one batch call per watcher), so a watch outliving a
  permission change leaks nothing. Deletions are exempt: there is no node left
  to check, and "it is gone" discloses nothing new.
- A failing check skips that watcher (fail closed) instead of failing the
  event, so one unreachable permission-service doesn't block the stream."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from favorite_service import watches

logger = logging.getLogger(__name__)

READ_PERMISSION = {"document": "document.read", "folder": "folder.read"}
WATCH_DURABLE = "favorite-service-watch"

PublishEvent = Callable[..., Awaitable[None]]


class ReadChecker:
    """`async (principal_id, object_type, object_ids) -> {object_id: bool}`."""

    def __init__(self, permission_client) -> None:
        self._client = permission_client

    async def __call__(self, principal_id: str, object_type: str, object_ids: list[str]) -> dict:
        return await self._client.check_batch(
            principal_id=principal_id,
            permission=READ_PERMISSION[object_type],
            access_type="read",
            resource_ids=object_ids,
        )


async def handle_event(
    event: Event,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    can_read: Callable[[str, str, list[str]], Awaitable[dict]],
    publish_event: PublishEvent,
) -> int:
    """Returns how many notifications were published (for tests and logs)."""
    changes = watches.changes_for(event.event_type, event.subject, event.payload or {})
    if not changes:
        return 0
    targets = list({(c.object_type, c.object_id) for c in changes})
    async with session_factory() as session:
        found = await watches.watchers_of(session, targets)
    if not found:
        return 0
    by_target: dict[tuple[str, str], list] = {}
    for watch in found:
        by_target.setdefault((watch.object_type, watch.object_id), []).append(watch)

    published = 0
    for change in changes:
        for watch in by_target.get((change.object_type, change.object_id), []):
            if event.actor and event.actor in (watch.principal_id, watch.user_id):
                continue
            if change.change not in watches.READ_CHECK_EXEMPT:
                try:
                    allowed = await can_read(
                        watch.principal_id, watch.object_type, [watch.object_id]
                    )
                except Exception:  # noqa: BLE001 - fail closed for this watcher
                    logger.warning("watch_read_check_failed", exc_info=True)
                    continue
                if not allowed.get(watch.object_id, False):
                    continue
            await publish_event(
                "favorite.watch.triggered",
                subject=watch.id,
                payload={
                    "user_id": watch.user_id,
                    "object_type": watch.object_type,
                    "object_id": watch.object_id,
                    "change": change.change,
                    "subject_type": change.subject_type,
                    "subject_id": change.subject_id,
                    "title": change.title,
                    "changed_by": event.actor,
                    "source_event_type": event.event_type,
                },
                actor="system:favorite-watch",
            )
            published += 1
    return published


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    can_read: Callable[[str, str, list[str]], Awaitable[dict]],
    publish_event: PublishEvent,
) -> None:
    """One durable consumer per stream. `deliver_new`: a new installation of
    this consumer must not replay the stream's whole history as notifications;
    after that, the durable resumes where it stopped."""

    async def handler(payload: bytes) -> None:
        await handle_event(
            Event.from_bytes(payload),
            session_factory=session_factory,
            can_read=can_read,
            publish_event=publish_event,
        )

    for subject in subjects:
        stream = subject.split(".", 1)[0]
        try:
            await bus.subscribe(
                subject, handler, durable=f"{WATCH_DURABLE}-{stream}", deliver_new=True
            )
        except SubjectNotFoundError:
            logger.warning("watch_stream_missing", extra={"subject": subject})
