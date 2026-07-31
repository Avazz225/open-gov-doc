import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from keycloak import KeycloakAdmin

from auth_service import superuser

logger = logging.getLogger(__name__)


def make_handler(
    admin: KeycloakAdmin,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Erster Konsument dieses Service überhaupt (P6-S5, 4.6): führt die
    Break-Glass-Aktivierung erst nach Genehmigung aus, exakt dasselbe
    Selbst-/Fremd-Konsum-Prinzip wie in ADR 0022 - dieser Service konsumiert
    sein eigenes, ihm zugeordnetes `action_type`, ignoriert alle anderen."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "auth.superuser.activate":
            return
        request_id = event.payload.get("request_id")
        try:
            expires_at = superuser.activate(admin, activation_minutes=activation_minutes)
        except superuser.SuperuserNotConfiguredError:
            logger.warning(
                "Genehmigte Break-Glass-Aktivierung konnte nicht ausgeführt werden - "
                "Superuser-Konto existiert nicht - request_id=%s",
                request_id,
            )
            return
        await publish_event(
            "auth.superuser.activated",
            {"request_id": request_id, "expires_at": expires_at.isoformat()},
        )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    admin: KeycloakAdmin,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(
        admin, activation_minutes=activation_minutes, publish_event=publish_event
    )
    for subject in subjects:
        try:
            await bus.subscribe(subject, handler, durable="auth-service")
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
