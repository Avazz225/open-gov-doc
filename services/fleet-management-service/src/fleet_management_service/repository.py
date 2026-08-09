import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fleet_management_service.models import ManagedInstallation
from fleet_management_service.schemas import ManagedInstallationCreate


class NotFoundError(Exception):
    pass


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


async def create_managed_installation(
    session: AsyncSession, payload: ManagedInstallationCreate
) -> tuple[ManagedInstallation, str]:
    now = datetime.now(UTC)
    api_key = payload.fleet_agent_api_key or generate_api_key()
    installation = ManagedInstallation(
        id=str(uuid.uuid4()),
        display_name=payload.display_name,
        gateway_base_url=payload.gateway_base_url,
        fleet_agent_api_key=api_key,
        created_at=now,
        updated_at=now,
    )
    session.add(installation)
    await session.flush()
    return installation, api_key


async def list_managed_installations(session: AsyncSession) -> list[ManagedInstallation]:
    result = await session.execute(
        select(ManagedInstallation).order_by(ManagedInstallation.display_name)
    )
    return list(result.scalars().all())


async def get_managed_installation(
    session: AsyncSession, installation_id: str
) -> ManagedInstallation:
    installation = await session.get(ManagedInstallation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    return installation


async def delete_managed_installation(session: AsyncSession, installation_id: str) -> None:
    installation = await get_managed_installation(session, installation_id)
    await session.delete(installation)
    await session.flush()
