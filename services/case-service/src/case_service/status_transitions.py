import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from case_service import repository
from case_service.models import Case
from case_service.object_type_client import ObjectTypeClient

logger = logging.getLogger(__name__)

PublishEvent = Callable[[str, str, dict, str | None], Awaitable[None]]


async def close_with_validation(
    session: AsyncSession,
    case: Case,
    *,
    object_type_client: ObjectTypeClient,
    snapshots: dict[str, int],
    publish_event: PublishEvent,
    actor: str | None,
) -> bool:
    """Gates `Case.status`'s "open"->"closed" transition through
    object-type-service's constraint engine (4.5/7.1, Phase 45 Session 4,
    ADR 0165's successor) before actually closing - the first real
    status-transition validation anywhere in this codebase. A no-op check
    (always allowed) when `case.object_type_id` is unset, same "absence =
    no restriction" default as every other optional per-object-type field.
    Shared between `main.py`'s synchronous branch (a fully-automated
    process completing inside `create_case`) and `consumer.py`'s
    `workflow.instance.completed` handler - the two existing call sites of
    `repository.close_case`.

    Returns whether the case was actually closed. On rejection, the case
    stays `"open"` (there is deliberately no automatic retry mechanism -
    `Case.attributes` is immutable after creation via the current API, so
    an object type declaring a transition-required attribute must have it
    supplied at creation time for auto-closure to ever succeed; a known,
    accepted limitation, see docs/services/case-service.md)."""
    if case.object_type_id is not None:
        errors = await object_type_client.validate(
            case.object_type_id,
            name=case.name,
            attributes=case.attributes,
            status_transition={"from": "open", "to": "closed"},
        )
        if errors:
            logger.warning(
                "Fall %r bleibt offen - Statusübergang open->closed vom Objekttyp abgelehnt: %s",
                case.id,
                errors,
            )
            await publish_event("case.close_blocked", case.id, {"errors": errors}, actor)
            return False

    await repository.close_case(session, case, snapshots=snapshots)
    return True
