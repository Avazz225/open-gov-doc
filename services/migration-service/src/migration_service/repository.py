import hmac
import secrets
import uuid
from datetime import UTC, datetime

from migration_service.models import InboundTransfer, PairedInstallation, Transfer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class NotFoundError(Exception):
    pass


class UnauthorizedError(Exception):
    """Fehlender/ungültiger `Authorization: Bearer`-Header bei einem
    eingehenden Aufruf von einer gepaarten Installation (7.2)."""


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


async def create_paired_installation(
    session: AsyncSession, *, display_name: str, base_url: str, api_key: str | None
) -> tuple[PairedInstallation, str]:
    key = api_key or generate_api_key()
    installation = PairedInstallation(
        id=str(uuid.uuid4()),
        display_name=display_name,
        base_url=base_url,
        api_key=key,
        created_at=datetime.now(UTC),
    )
    session.add(installation)
    await session.flush()
    return installation, key


async def get_paired_installation(
    session: AsyncSession, installation_id: str
) -> PairedInstallation:
    installation = await session.get(PairedInstallation, installation_id)
    if installation is None:
        raise NotFoundError(f"installation_id {installation_id!r} unbekannt")
    return installation


async def list_paired_installations(session: AsyncSession) -> list[PairedInstallation]:
    result = await session.execute(
        select(PairedInstallation).order_by(PairedInstallation.created_at.desc())
    )
    return list(result.scalars().all())


async def delete_paired_installation(session: AsyncSession, installation_id: str) -> None:
    installation = await get_paired_installation(session, installation_id)
    await session.delete(installation)
    await session.flush()


async def authenticate_peer(session: AsyncSession, presented_key: str | None) -> PairedInstallation:
    """Ordnet einen eingehenden `Authorization: Bearer`-Wert einer gepaarten
    Installation zu (7.2) - `hmac.compare_digest` statt `==`, um
    Timing-Seitenkanäle beim Key-Vergleich zu vermeiden (derselbe Grund wie
    bei jedem anderen Secret-Vergleich im Projekt, z. B. Passwort-Hashes)."""
    if not presented_key:
        raise UnauthorizedError("Authorization: Bearer-Header fehlt")
    result = await session.execute(select(PairedInstallation))
    for installation in result.scalars().all():
        if hmac.compare_digest(installation.api_key, presented_key):
            return installation
    raise UnauthorizedError("API-Key gehört zu keiner gepaarten Installation")


async def create_transfer(
    session: AsyncSession,
    *,
    source_folder_id: str,
    target_installation_id: str,
    created_by: str,
    dry_run: bool,
    retention_days: int,
) -> Transfer:
    now = datetime.now(UTC)
    transfer = Transfer(
        id=str(uuid.uuid4()),
        source_folder_id=source_folder_id,
        target_installation_id=target_installation_id,
        dry_run=dry_run,
        retention_days=retention_days,
        status="pending",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def get_transfer(session: AsyncSession, transfer_id: str) -> Transfer:
    transfer = await session.get(Transfer, transfer_id)
    if transfer is None:
        raise NotFoundError(f"transfer_id {transfer_id!r} unbekannt")
    return transfer


async def list_transfers(session: AsyncSession, *, status: str | None = None) -> list[Transfer]:
    query = select(Transfer)
    if status is not None:
        query = query.where(Transfer.status == status)
    result = await session.execute(query.order_by(Transfer.created_at.desc()))
    return list(result.scalars().all())


async def set_workflow_instance(session: AsyncSession, transfer_id: str, instance_id: str) -> None:
    transfer = await get_transfer(session, transfer_id)
    transfer.workflow_instance_id = instance_id
    transfer.updated_at = datetime.now(UTC)
    await session.flush()


async def create_inbound_transfer(
    session: AsyncSession, *, transfer_id: str, source_installation_id: str, target_folder_id: str
) -> InboundTransfer:
    inbound = InboundTransfer(
        id=transfer_id,
        source_installation_id=source_installation_id,
        target_folder_id=target_folder_id,
        created_at=datetime.now(UTC),
    )
    session.add(inbound)
    await session.flush()
    return inbound


async def get_inbound_transfer(session: AsyncSession, transfer_id: str) -> InboundTransfer:
    inbound = await session.get(InboundTransfer, transfer_id)
    if inbound is None:
        raise NotFoundError(f"transfer_id {transfer_id!r} (inbound) unbekannt")
    return inbound
