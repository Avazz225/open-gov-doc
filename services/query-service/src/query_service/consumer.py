import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

from query_service import manipulation

logger = logging.getLogger(__name__)


def make_handler(
    clients: manipulation.ManipulationClients,
    publish_event: Callable[[str, str | None, dict, str | None], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Fuehrt zuvor per Vier-Augen-Prinzip zurueckgestellte Manipulations-
    Aktionen erst nach Genehmigung aus - identisches Muster wie
    `document-service`/`auth-service` (ADR 0022). Andere Aktionstypen
    (z. B. Force-Unlock) gehoeren nicht zu diesem Service und werden
    ignoriert."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type not in manipulation.ACTIONS:
            return

        action_payload = event.payload.get("payload") or {}
        params = action_payload.get("params")
        principal_id = action_payload.get("principal_id")
        if params is None:
            logger.warning(
                "permission.approval.approved fuer %r ohne params im payload erhalten - "
                "ignoriert: %r",
                action_type,
                action_payload,
            )
            return

        action = manipulation.get_action(action_type)
        try:
            result = await action.execute(params, clients)
        except Exception:
            logger.exception(
                "Genehmigte Manipulations-Aktion %r (params=%r) konnte nicht ausgefuehrt werden.",
                action_type,
                params,
            )
            await publish_event(
                "query.manipulation.execution_failed",
                None,
                {"action_type": action_type, "params": params},
                principal_id,
            )
            return

        await publish_event(
            "query.manipulation.executed",
            None,
            {"action_type": action_type, "params": params, "result": result},
            principal_id,
        )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    clients: manipulation.ManipulationClients,
    publish_event: Callable[[str, str | None, dict, str | None], Awaitable[None]],
) -> None:
    handler = make_handler(clients, publish_event)
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="query-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream fuer Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum naechsten Neustart nicht konsumiert.",
                subject,
            )
