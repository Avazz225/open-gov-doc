from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from query_service.models import MANIPULATION_MODE_STATUS_ID, ManipulationModeStatus


async def _get_or_create(session: AsyncSession) -> ManipulationModeStatus:
    status = await session.get(ManipulationModeStatus, MANIPULATION_MODE_STATUS_ID)
    if status is None:
        status = ManipulationModeStatus(
            id=MANIPULATION_MODE_STATUS_ID, active=False, updated_at=datetime.now(UTC)
        )
        session.add(status)
    return status


async def get_status(session: AsyncSession) -> ManipulationModeStatus:
    return await _get_or_create(session)


def is_active(status: ManipulationModeStatus) -> bool:
    """Lazy-Ablauf-Pruefung (Konzept 6.1 Punkt 1) - kein Poll-Loop noetig,
    ein abgelaufener Schutzschalter blockiert einfach den naechsten
    Schreibversuch, es gibt nichts aufzuraeumen (anders als Break-Glass,
    dessen Ablauf einen Keycloak-Account wieder deaktivieren muss)."""
    if not status.active or status.expires_at is None:
        return False
    return status.expires_at > datetime.now(UTC)


async def activate(
    session: AsyncSession, *, activated_by: str, duration_minutes: int
) -> ManipulationModeStatus:
    status = await _get_or_create(session)
    status.active = True
    status.activated_by = activated_by
    status.expires_at = datetime.now(UTC) + timedelta(minutes=duration_minutes)
    status.updated_at = datetime.now(UTC)
    return status


async def deactivate(session: AsyncSession) -> ManipulationModeStatus:
    status = await _get_or_create(session)
    status.active = False
    status.expires_at = None
    status.updated_at = datetime.now(UTC)
    return status
