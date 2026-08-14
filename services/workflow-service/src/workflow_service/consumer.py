import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

logger = logging.getLogger(__name__)


def make_handler(
    apply_import: Callable[[str, str, str | None], Awaitable[object]],
) -> Callable[[bytes], Awaitable[None]]:
    """Führt einen zuvor per Vier-Augen-Prinzip (4.3, Post-Roadmap Phase 21
    Session 4, ADR 0087) zurückgestellten BPMN-Import erst nach Genehmigung
    aus - reiner Konsument (`ensure_stream=False` in `main.py`s Lifespan):
    workflow-service besitzt dafür keinen eigenen Stream (sein eigener
    `event_bus`/Stream `"workflow"` ist ein separater Producer für
    `workflow.*`-Events, siehe `main.py`), hier reagiert es nur auf
    permission-services bereits bestehendes `permission.approval.approved`-
    Event, identisches Selbst-/Fremd-Konsum-Muster wie `config_service.
    consumer`. Andere Aktionstypen gehören nicht zu diesem Service und
    werden ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "workflow.process_definition.import":
            return
        action_payload = event.payload.get("payload") or {}
        name = action_payload.get("name")
        bpmn_xml = action_payload.get("bpmn_xml")
        if not name or not bpmn_xml:
            # Fremd-/Fehlform-Payload (z. B. ein zu Testzwecken über
            # /approval-requests angelegter Request mit demselben
            # action_type, aber ohne name/bpmn_xml) - loggen statt crashen,
            # sonst bleibt die NATS-Nachricht unbestätigt und wird endlos
            # erneut zugestellt (siehe dms-eventbus-client._callback).
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
