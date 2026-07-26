from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from registry_service.models import ServiceInstance
from registry_service.schemas import InstanceOut, RegisterRequest


class InstanceNotFoundError(Exception):
    pass


def _is_healthy(instance: ServiceInstance, timeout_seconds: float, now: datetime) -> bool:
    age = (now - instance.last_heartbeat_at).total_seconds()
    return age <= timeout_seconds


def _to_out(instance: ServiceInstance, timeout_seconds: float, now: datetime) -> InstanceOut:
    return InstanceOut(
        instance_id=instance.instance_id,
        service_type=instance.service_type,
        version=instance.version,
        capabilities=instance.capabilities,
        health_endpoint=instance.health_endpoint,
        address=instance.address,
        registered_at=instance.registered_at,
        last_heartbeat_at=instance.last_heartbeat_at,
        healthy=_is_healthy(instance, timeout_seconds, now),
    )


async def register(session: AsyncSession, payload: RegisterRequest) -> InstanceOut:
    """Registrierung ist ein Upsert (3.2a): meldet sich eine bereits bekannte
    ``instance_id`` erneut (z. B. nach einem Reconnect), werden ihre Daten
    aktualisiert statt einen Konflikt zu erzeugen.
    """
    now = datetime.now(UTC)
    instance = await session.get(ServiceInstance, payload.instance_id)
    if instance is None:
        instance = ServiceInstance(instance_id=payload.instance_id, registered_at=now)
        session.add(instance)

    instance.service_type = payload.service_type
    instance.version = payload.version
    instance.capabilities = payload.capabilities
    instance.health_endpoint = payload.health_endpoint
    instance.address = payload.address
    instance.last_heartbeat_at = now

    await session.flush()
    return _to_out(instance, timeout_seconds=0, now=now)


async def heartbeat(session: AsyncSession, instance_id: str) -> InstanceOut:
    instance = await session.get(ServiceInstance, instance_id)
    if instance is None:
        raise InstanceNotFoundError(instance_id)
    now = datetime.now(UTC)
    instance.last_heartbeat_at = now
    await session.flush()
    return _to_out(instance, timeout_seconds=0, now=now)


async def deregister(session: AsyncSession, instance_id: str) -> None:
    instance = await session.get(ServiceInstance, instance_id)
    if instance is None:
        raise InstanceNotFoundError(instance_id)
    await session.delete(instance)
    await session.flush()


async def list_active_by_type(
    session: AsyncSession, service_type: str, *, heartbeat_timeout_seconds: float
) -> list[InstanceOut]:
    """Die aktive Routingtabelle (3.2a): nur Instanzen, deren letzter Heartbeat
    innerhalb des konfigurierten Zeitfensters liegt. Kein separater
    Hintergrund-Sweep nötig - Ausfall wird beim Lesen bewertet, nicht durch
    einen mutierenden Hintergrundjob, was Race Conditions vermeidet.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(ServiceInstance).where(ServiceInstance.service_type == service_type)
    )
    instances = result.scalars().all()
    return [
        _to_out(i, heartbeat_timeout_seconds, now)
        for i in instances
        if _is_healthy(i, heartbeat_timeout_seconds, now)
    ]


async def list_all(session: AsyncSession, *, heartbeat_timeout_seconds: float) -> list[InstanceOut]:
    now = datetime.now(UTC)
    result = await session.execute(select(ServiceInstance))
    instances = result.scalars().all()
    return [_to_out(i, heartbeat_timeout_seconds, now) for i in instances]
