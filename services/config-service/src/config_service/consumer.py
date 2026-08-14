import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

logger = logging.getLogger(__name__)


def make_handler(
    apply_import: Callable[[dict, list[str] | None], Awaitable[object]],
) -> Callable[[bytes], Awaitable[None]]:
    """Executes an import previously deferred via the four-eyes principle (4.3,
    since P17-S3, 14.2 "Configuration Import") only after approval
    - pure consumer (`ensure_stream=False` in `main.py`'s lifespan):
    config-service owns no stream of its own and has nothing of its own to
    publish, it only reacts to permission-service's already existing
    `permission.approval.approved` event, an identical self-/foreign-consumption
    pattern to `document_service.consumer`. Other action types (e.g.
    scope locks, role assignments) do not belong to this service and
    are ignored. `apply_import` is `main._apply_config_document` -
    the same application logic as the immediate, ungated path."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "config.import":
            return
        action_payload = event.payload.get("payload") or {}
        document = action_payload.get("document")
        if document is None:
            # Foreign/malformed payload (e.g. a request created for test purposes
            # via /approval-requests with the same
            # action_type but without document) - log instead of crashing, otherwise
            # the NATS message stays unacknowledged and gets redelivered
            # endlessly (see dms-eventbus-client._callback).
            logger.warning(
                "permission.approval.approved für config.import ohne document im "
                "payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        try:
            await apply_import(document, action_payload.get("categories"))
        except Exception:
            # Broader than the usual `(repository.NotFoundError, KeyError)`
            # of other consumers: `_apply_config_document` can, among other things,
            # raise `HTTPException` (unknown schema version) as well as
            # propagate arbitrary downstream client errors, which no longer have
            # an HTTP caller here to report them to -
            # individual category entries already fail best-effort within
            # `imports.apply_import` anyway (see there).
            logger.exception(
                "Genehmigter Konfigurationsimport (request_id=%s) konnte nicht angewendet werden",
                event.payload.get("request_id"),
            )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    apply_import: Callable[[dict, list[str] | None], Awaitable[object]],
) -> None:
    handler = make_handler(apply_import)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="config-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
