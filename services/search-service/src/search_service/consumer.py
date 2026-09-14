import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from search_service import repository
from search_service.document_client import DocumentServiceClient
from search_service.folder_client import FolderServiceClient
from search_service.ocr_client import OcrServiceClient
from search_service.pipeline import reindex_document
from search_service.rendering_client import RenderingServiceClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def make_document_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    """Reacts to `document.>` (3.1) - document events are deliberately thin
    (see pipeline.py), so on every relevant event the full state is
    reloaded via HTTP instead of read from the event payload."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        document_id = event.subject
        if document_id is None:
            return

        if event.event_type == "document.deleted":
            async with session_factory() as session:
                await repository.delete_document(session, document_id)
                await session.commit()
            return

        if event.event_type not in (
            "document.created",
            "document.version.created",
            "document.metadata.updated",
            # Since Post-Roadmap Phase 35 Session 4 (ADR 0146): both change
            # `registered_at` (and `document.promoted` additionally moves
            # `folder_id`) - a work-tray document must disappear from the
            # `registered=false` index promptly, not just on its next
            # unrelated metadata touch.
            "document.registered",
            "document.promoted",
        ):
            return

        await reindex_document(
            document_id,
            session_factory=session_factory,
            document_client=document_client,
            folder_client=folder_client,
            ocr_client=ocr_client,
            rendering_client=rendering_client,
        )

    return handle


def make_text_update_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    """Reacts to `ocr.completed` and `rendering.completed`
    (`rendition_type == "substitute_text"` only) - re-indexing, since OCR/
    rendering complete after the initial upload event, timewise. Calls the
    same `reindex_document()` as the document handler (see its docstring on
    the backfill race between streams)."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        document_id = event.subject
        if document_id is None:
            return

        if event.event_type == "ocr.completed":
            if event.payload.get("status") not in ("ready", "needs_review"):
                return
        elif event.event_type == "rendering.completed":
            if event.payload.get("rendition_type") != "substitute_text":
                return
            if event.payload.get("status") != "ready":
                return
        else:
            return

        await reindex_document(
            document_id,
            session_factory=session_factory,
            document_client=document_client,
            folder_client=folder_client,
            ocr_client=ocr_client,
            rendering_client=rendering_client,
        )

    return handle


async def start_consuming_documents(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> None:
    handler = make_document_handler(
        session_factory=session_factory,
        document_client=document_client,
        folder_client=folder_client,
        ocr_client=ocr_client,
        rendering_client=rendering_client,
    )
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="search-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )


async def start_consuming_text_updates(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    document_client: DocumentServiceClient,
    folder_client: FolderServiceClient,
    ocr_client: OcrServiceClient,
    rendering_client: RenderingServiceClient,
) -> None:
    handler = make_text_update_handler(
        session_factory=session_factory,
        document_client=document_client,
        folder_client=folder_client,
        ocr_client=ocr_client,
        rendering_client=rendering_client,
    )
    for subject in subjects:
        try:
            # Own durable name, separate from the document.> subscription -
            # both run through the same event_bus client, but on different
            # streams.
            await bus.subscribe(subject, handler, durable="search-service-text")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )


def make_folder_reference_handler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    folder_client: FolderServiceClient,
) -> Callable[[bytes], Awaitable[None]]:
    """Reacts to `folder.document_reference.>` (hand folders, ADR 0118) -
    Post-Roadmap Phase 35 Session 4 (ADR 0146). Unlike the document handler
    above, does NOT reload the full state via HTTP - the one endpoint that
    would offer it (`GET /folders/{id}/document-references`) is itself
    `folder.read`-gated (the actual point of ADR 0118), so `added_at`
    travels in the event payload instead (folder-service change, see its
    `main.py`). Only `folder_name` is denormalized via the (ungated)
    `GET /folders/{id}`, same as the document handler already does."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        folder_id = event.subject
        if folder_id is None:
            return
        document_id = event.payload.get("document_id")
        if not document_id:
            return

        if event.event_type == "folder.document_reference.removed":
            async with session_factory() as session:
                await repository.delete_folder_reference(session, folder_id, document_id)
                await session.commit()
            return

        if event.event_type != "folder.document_reference.added":
            return

        folder = await folder_client.get(folder_id)
        folder_name = folder["name"] if folder else None
        added_at_raw = event.payload.get("added_at")
        added_at = datetime.fromisoformat(added_at_raw) if added_at_raw else datetime.now(UTC)

        async with session_factory() as session:
            await repository.upsert_folder_reference(
                session,
                folder_id=folder_id,
                document_id=document_id,
                folder_name=folder_name,
                added_by=event.payload.get("added_by", "?"),
                added_at=added_at,
            )
            await session.commit()

    return handle


async def start_consuming_folder_references(
    bus: NatsEventBusClient,
    subjects: list[str],
    *,
    session_factory: async_sessionmaker[AsyncSession],
    folder_client: FolderServiceClient,
) -> None:
    handler = make_folder_reference_handler(
        session_factory=session_factory, folder_client=folder_client
    )
    for subject in subjects:
        try:
            # Own durable name - third subscription in this service, own
            # "folder" stream (not shared with document.>/ocr.>/rendering.>).
            # `deliver_new=True` (unlike every other consumer in this
            # service) - deliberately NOT a full-history replay-as-backfill:
            # `folder.document_reference.*` has existed since ADR 0118
            # (Post-Roadmap Phase 31 Session 7), long before this consumer,
            # so a first-ever subscribe here would otherwise replay an
            # unbounded amount of history at startup - a real cost that only
            # grows with a long-lived installation's age, not something this
            # "simple overview page" session's scope calls for solving via
            # an open-ended replay. Accepted consequence: a hand-folder
            # reference added BEFORE this feature's rollout is not
            # retroactively backfilled into the index (see ADR 0146) - the
            # source table in folder-service remains the system of record
            # regardless.
            await bus.subscribe(
                subject, handler, durable="search-service-folder-references", deliver_new=True
            )
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
