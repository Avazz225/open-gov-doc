import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

logger = logging.getLogger(__name__)


def make_handler(
    apply_import: Callable[[dict, list[str] | None], Awaitable[object]],
) -> Callable[[bytes], Awaitable[None]]:
    """Führt einen zuvor per Vier-Augen-Prinzip (4.3, seit P17-S3, 14.2
    "Konfigurationsimport") zurückgestellten Import erst nach Genehmigung aus
    - reiner Konsument (`ensure_stream=False` in `main.py`s Lifespan):
    config-service besitzt keinen eigenen Stream und hat nichts Eigenes zu
    publizieren, es reagiert nur auf permission-services bereits bestehendes
    `permission.approval.approved`-Event, identisches Selbst-/Fremd-Konsum-
    Muster wie `document_service.consumer`. Andere Aktionstypen (z. B.
    Bereichssperren, Rollenzuweisungen) gehören nicht zu diesem Service und
    werden ignoriert. `apply_import` ist `main._apply_config_document` -
    dieselbe Anwendungslogik wie der sofortige, ungegatete Pfad."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "config.import":
            return
        action_payload = event.payload.get("payload") or {}
        document = action_payload.get("document")
        if document is None:
            # Fremd-/Fehlform-Payload (z. B. ein zu Testzwecken über
            # /approval-requests angelegter Request mit demselben
            # action_type, aber ohne document) - loggen statt crashen, sonst
            # bleibt die NATS-Nachricht unbestätigt und wird endlos erneut
            # zugestellt (siehe dms-eventbus-client._callback).
            logger.warning(
                "permission.approval.approved für config.import ohne document im "
                "payload erhalten - ignoriert: %r",
                action_payload,
            )
            return

        try:
            await apply_import(document, action_payload.get("categories"))
        except Exception:
            # Breiter als das übliche `(repository.NotFoundError, KeyError)`
            # anderer Konsumenten: `_apply_config_document` kann u. a.
            # `HTTPException` (unbekannte Schema-Version) werfen sowie
            # beliebige Downstream-Client-Fehler propagieren, die hier keinen
            # HTTP-Aufrufer mehr haben, dem sie gemeldet werden könnten -
            # einzelne Kategorie-Einträge scheitern ohnehin bereits
            # best-effort innerhalb von `imports.apply_import` (siehe dort).
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
