import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError

from auth_service import superuser

logger = logging.getLogger(__name__)


def make_handler(
    session_factory,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """First consumer of this service ever (P6-S5, 4.6): executes the
    break-glass activation only after approval, exactly the same
    self/foreign-consumption principle as in ADR 0022 - this service
    consumes its own, assigned `action_type`, ignoring all others.
    `session_factory` instead of `KeycloakAdmin` since Phase 18 (ADR 0063) -
    the superuser has lived as a DB row rather than a Keycloak account
    ever since."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.payload.get("action_type") != "auth.superuser.activate":
            return
        request_id = event.payload.get("request_id")
        try:
            expires_at = await superuser.activate(
                session_factory, activation_minutes=activation_minutes
            )
        except superuser.SuperuserNotConfiguredError:
            logger.warning(
                "Genehmigte Break-Glass-Aktivierung konnte nicht ausgeführt werden - "
                "Superuser-Konto existiert nicht - request_id=%s",
                request_id,
            )
            return
        # Passes through the actor of the approving permission.approval.approved
        # event (since P7-S2) - that's the person who actually approved the
        # activation.
        await publish_event(
            "auth.superuser.activated",
            {"request_id": request_id, "expires_at": expires_at.isoformat()},
            actor=event.actor,
        )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(
        session_factory, activation_minutes=activation_minutes, publish_event=publish_event
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
