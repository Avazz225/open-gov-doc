"""Watch subscriptions (P90-S4, ADR 0256): persistence, and matching
document-/folder-service events to the watches they concern.

`changes_for` is pure: it maps one event to the watched objects it touches
and a change code, without I/O. The consumer (`watch_consumer.py`) looks up
the watchers, checks they can still read the object, and publishes one
`favorite.watch.triggered` per watcher for notification-service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from favorite_service.models import Watch

# Change codes; notification-service words them. Deletions are told even
# when the object is no longer readable (see `READ_CHECK_EXEMPT`).
DOCUMENT_EVENTS = {
    "document.version.created": "new_version",
    "document.metadata.updated": "metadata_updated",
    "document.resource.moved": "moved",
    "document.deleted": "trashed",
    "document.restored": "restored",
    "document.force_deleted": "deleted",
    "document.trash_purged": "deleted",
}
FOLDER_EVENTS = {
    "folder.updated": "folder_updated",
    "folder.resource.moved": "moved",
    "folder.trashed": "trashed",
    "folder.restored": "restored",
    "folder.force_deleted": "deleted",
    "folder.trash_purged": "deleted",
}
# Changes inside a watched folder (direct children, not the whole subtree).
DOCUMENT_ADDED = "document_added"
DOCUMENT_MOVED_IN = "document_moved_in"

# A deleted object has no permission node left to check against; telling a
# watcher that it is gone discloses nothing they didn't watch.
READ_CHECK_EXEMPT = {"deleted"}


class DuplicateError(Exception):
    pass


class NotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Change:
    object_type: str  # the WATCHED object's type
    object_id: str
    change: str
    # The object the change is about, when it differs from the watched one
    # (a document added to a watched folder).
    subject_type: str
    subject_id: str
    title: str | None = None


def changes_for(event_type: str, subject: str | None, payload: dict) -> list[Change]:
    """The watched objects one event concerns, with what happened to them."""
    if not subject:
        return []
    changes: list[Change] = []
    title = payload.get("title") or payload.get("name")
    if event_type in DOCUMENT_EVENTS:
        changes.append(
            Change("document", subject, DOCUMENT_EVENTS[event_type], "document", subject, title)
        )
    if event_type in FOLDER_EVENTS:
        changes.append(
            Change("folder", subject, FOLDER_EVENTS[event_type], "folder", subject, title)
        )
    if event_type == "document.created" and payload.get("folder_id"):
        changes.append(
            Change("folder", payload["folder_id"], DOCUMENT_ADDED, "document", subject, title)
        )
    if event_type == "document.resource.moved":
        target = payload.get("new_parent_id")
        if target and target != "root":
            changes.append(Change("folder", target, DOCUMENT_MOVED_IN, "document", subject, title))
    return changes


async def create_watch(
    session: AsyncSession,
    *,
    user_id: str,
    principal_id: str,
    object_type: str,
    object_id: str,
    now: datetime,
) -> Watch:
    existing = await session.execute(
        select(Watch).where(
            Watch.user_id == user_id,
            Watch.object_type == object_type,
            Watch.object_id == object_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise DuplicateError(f"{object_type} {object_id!r} wird bereits beobachtet")
    watch = Watch(
        user_id=user_id,
        principal_id=principal_id,
        object_type=object_type,
        object_id=object_id,
        created_at=now,
    )
    session.add(watch)
    await session.flush()
    return watch


async def delete_watch(
    session: AsyncSession, *, user_id: str, object_type: str, object_id: str
) -> None:
    result = await session.execute(
        select(Watch).where(
            Watch.user_id == user_id,
            Watch.object_type == object_type,
            Watch.object_id == object_id,
        )
    )
    watch = result.scalar_one_or_none()
    if watch is None:
        raise NotFoundError(f"{object_type} {object_id!r} wird nicht beobachtet")
    await session.delete(watch)


async def list_watches(
    session: AsyncSession,
    *,
    user_id: str,
    object_type: str | None = None,
    object_id: str | None = None,
) -> list[Watch]:
    query = select(Watch).where(Watch.user_id == user_id)
    if object_type is not None:
        query = query.where(Watch.object_type == object_type)
    if object_id is not None:
        query = query.where(Watch.object_id == object_id)
    result = await session.execute(query.order_by(Watch.created_at.desc()))
    return list(result.scalars().all())


async def watchers_of(session: AsyncSession, targets: list[tuple[str, str]]) -> list[Watch]:
    """Every watch on any of the `(object_type, object_id)` targets."""
    if not targets:
        return []
    result = await session.execute(
        select(Watch).where(tuple_(Watch.object_type, Watch.object_id).in_(targets))
    )
    return list(result.scalars().all())
