import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

logger = logging.getLogger(__name__)


def make_handler(
    apply_import: Callable[[str, str, str | None], Awaitable[object]],
) -> Callable[[bytes], Awaitable[None]]:
    """Executes a BPMN import previously deferred via the four-eyes
    principle (4.3, post-roadmap phase 21 session 4, ADR 0087) only after
    approval - pure consumer (`ensure_stream=False` in `main.py`'s
    lifespan): workflow-service owns no stream of its own for this (its own
    `event_bus`/stream `"workflow"` is a separate producer for
    `workflow.*` events, see `main.py`); here it merely reacts to
    permission-service's already existing `permission.approval.approved`
    event, the same self-/foreign-consumption pattern as `config_service.
    consumer`. Other action types do not belong to this service and are
    ignored."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "workflow.process_definition.import":
            return
        action_payload = event.payload.get("payload") or {}
        name = action_payload.get("name")
        bpmn_xml = action_payload.get("bpmn_xml")
        if not name or not bpmn_xml:
            # Foreign/malformed payload (e.g. a request created for testing
            # purposes via /approval-requests with the same action_type but
            # without name/bpmn_xml) - log instead of crashing, otherwise
            # the NATS message remains unacknowledged and gets redelivered
            # endlessly (see dms-eventbus-client._callback).
            logger.warning(
                "permission.approval.approved für workflow.process_definition.import ohne "
                "name/bpmn_xml im payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        try:
            await apply_import(name, bpmn_xml, action_payload.get("process_id"))
        except Exception:
            logger.exception(
                "Genehmigter BPMN-Import (request_id=%s) konnte nicht angewendet werden",
                event.payload.get("request_id"),
            )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    apply_import: Callable[[str, str, str | None], Awaitable[object]],
) -> None:
    handler = make_handler(apply_import)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="workflow-service-approval")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
