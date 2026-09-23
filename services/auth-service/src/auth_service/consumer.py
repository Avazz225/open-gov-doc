import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakGetError

from auth_service import ad_group_mapping, admin_users, superuser, tracking

logger = logging.getLogger(__name__)

_KNOWN_ACTION_TYPES = (
    "auth.superuser.activate",
    "auth.ad_group_role_mapping.create",
    "auth.ad_group_role_mapping.delete",
    "auth.ad_group_role_composite_rule.create",
    "auth.ad_group_role_composite_rule.delete",
    "auth.ad_group_mapping.default_role_set",
    "auth.user_tracking_config.update",
)


def _resolve_display_name(keycloak_admin: KeycloakAdmin, principal_id: str | None) -> str | None:
    """Resolves the initiator's raw Keycloak `sub` into their username
    (Phase 53 Session 1, ADR 0171) - the direct/ungated path already stores
    `user.get("preferred_username") or principal_id` as `created_by`, but
    this consumer previously only had the raw `sub` from the approval
    event's `initiated_by` field to work with, so every four-eyes-executed
    mapping/rule ended up with a raw UUID instead. Reuses the exact same
    reverse-resolution primitive `GET /users/{user_id}` already established
    (`admin_users.find_user_by_id`, ADR 0069) rather than inventing a new
    one or extending the approval-request payload itself (the "larger
    change" ADR 0153 declined to attempt) - a plain server-side lookup at
    execution time needs neither. Falls back to the raw id, same as the
    direct path's own `or principal_id` fallback - e.g. a `TechnicalAccount`
    initiator's `sub` is a local integer row id, not a Keycloak UUID, and
    404s here exactly as expected."""
    if principal_id is None:
        return None
    try:
        match = admin_users.find_user_by_id(keycloak_admin, principal_id)
    except KeycloakGetError:
        return principal_id
    return match["username"] if match is not None else principal_id


def make_handler(
    session_factory,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
    keycloak_admin: KeycloakAdmin,
) -> Callable[[bytes], Awaitable[None]]:
    """First consumer of this service ever (P6-S5, 4.6): executes the
    break-glass activation only after approval, exactly the same
    self/foreign-consumption principle as in ADR 0022 - this service
    consumes its own, assigned `action_type`, ignoring all others.
    `session_factory` instead of `KeycloakAdmin` since Phase 18 (ADR 0063) -
    the superuser has lived as a DB row rather than a Keycloak account
    ever since.

    Extended Post-Roadmap Phase 39 Session 3 (ADR 0153) with the four
    AD-group-mapping action types - unlike break-glass, these use the
    OPTIONAL, per-action-type-configurable four-eyes pattern (`main.py`'s
    `_maybe_defer_to_approval`), so this consumer only ever sees an event
    for one of them when an admin actually turned on approval for that
    specific action type. Extended again in Phase 53 Session 1 (ADR 0171)
    with a fifth action type (the default-role setting) and `keycloak_admin`
    (see `_resolve_display_name` above) for the `created_by`/`updated_by`
    display-name fix. Extended again in Post-Roadmap Phase 74 Session 2
    with a sixth action type (`auth.user_tracking_config.update`, ADR
    0157's own named gap), same optional-four-eyes/display-name-resolution
    shape as the AD-group-mapping ones."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        action_type = event.payload.get("action_type")
        if action_type not in _KNOWN_ACTION_TYPES:
            return
        request_id = event.payload.get("request_id")
        action_payload = event.payload.get("payload") or {}

        if action_type == "auth.superuser.activate":
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
            return

        async with session_factory() as session:
            try:
                if action_type == "auth.ad_group_role_mapping.create":
                    mapping = await ad_group_mapping.create_mapping(
                        session,
                        ad_group_name=action_payload["ad_group_name"],
                        role_name=action_payload["role_name"],
                        created_by=_resolve_display_name(
                            keycloak_admin, event.payload.get("initiated_by")
                        ),
                    )
                    await session.commit()
                    await publish_event(
                        "auth.ad_group_role_mapping.created",
                        {
                            "id": mapping.id,
                            "ad_group_name": mapping.ad_group_name,
                            "role_name": mapping.role_name,
                        },
                        actor=event.actor,
                    )
                elif action_type == "auth.ad_group_role_mapping.delete":
                    mapping = await ad_group_mapping.delete_mapping(
                        session, action_payload["mapping_id"]
                    )
                    await session.commit()
                    await publish_event(
                        "auth.ad_group_role_mapping.deleted",
                        {
                            "id": action_payload["mapping_id"],
                            "ad_group_name": mapping.ad_group_name,
                            "role_name": mapping.role_name,
                        },
                        actor=event.actor,
                    )
                elif action_type == "auth.ad_group_role_composite_rule.create":
                    rule = await ad_group_mapping.create_composite_rule(
                        session,
                        role_name=action_payload["role_name"],
                        ad_group_names=action_payload["ad_group_names"],
                        created_by=_resolve_display_name(
                            keycloak_admin, event.payload.get("initiated_by")
                        ),
                    )
                    await session.commit()
                    await publish_event(
                        "auth.ad_group_role_composite_rule.created", rule, actor=event.actor
                    )
                elif action_type == "auth.ad_group_role_composite_rule.delete":
                    rule = await ad_group_mapping.delete_composite_rule(
                        session, action_payload["rule_id"]
                    )
                    await session.commit()
                    await publish_event(
                        "auth.ad_group_role_composite_rule.deleted", rule, actor=event.actor
                    )
                elif action_type == "auth.ad_group_mapping.default_role_set":
                    config = await ad_group_mapping.set_default_role(
                        session,
                        default_role_name=action_payload["default_role_name"],
                        updated_by=_resolve_display_name(
                            keycloak_admin, event.payload.get("initiated_by")
                        ),
                    )
                    await session.commit()
                    await publish_event(
                        "auth.ad_group_mapping.default_role_set",
                        {"default_role_name": config.default_role_name},
                        actor=event.actor,
                    )
                elif action_type == "auth.user_tracking_config.update":
                    # Post-Roadmap Phase 74 Session 2 (ADR 0157's own named
                    # gap) - same shape as the AD-group-mapping handlers
                    # above, `updated_by` resolved to a display name via
                    # the same reverse-Keycloak-lookup helper.
                    config = await tracking.set_tracking_enabled(
                        session,
                        action_payload["principal_id"],
                        enabled=action_payload["enabled"],
                        updated_by=_resolve_display_name(
                            keycloak_admin, event.payload.get("initiated_by")
                        ),
                    )
                    await session.commit()
                    await publish_event(
                        "auth.user_tracking.config_changed",
                        {
                            "principal_id": config.principal_id,
                            "enabled": config.enabled,
                        },
                        actor=event.actor,
                    )
            except (
                ad_group_mapping.MappingNotFoundError,
                ad_group_mapping.CompositeRuleNotFoundError,
                ad_group_mapping.TooFewGroupsError,
                KeyError,
            ):
                # Same reasoning as permission-service's own approval_consumer.py:
                # log instead of crashing, otherwise the NATS message stays
                # unacknowledged and gets redelivered endlessly.
                logger.warning(
                    "Genehmigte Aktion %r konnte nicht ausgeführt werden (Zeile inzwischen "
                    "nicht mehr vorhanden oder Payload unvollständig) - request_id=%s, "
                    "payload=%r",
                    action_type,
                    request_id,
                    action_payload,
                )

    return handle


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory,
    *,
    activation_minutes: int,
    publish_event: Callable[[str, dict], Awaitable[None]],
    keycloak_admin: KeycloakAdmin,
) -> None:
    handler = make_handler(
        session_factory,
        activation_minutes=activation_minutes,
        publish_event=publish_event,
        keycloak_admin=keycloak_admin,
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
