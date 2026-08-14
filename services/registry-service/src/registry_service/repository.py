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
        sensors=instance.sensors,
        health_endpoint=instance.health_endpoint,
        address=instance.address,
        registered_at=instance.registered_at,
        last_heartbeat_at=instance.last_heartbeat_at,
        healthy=_is_healthy(instance, timeout_seconds, now),
        status=instance.status,
    )


async def register(session: AsyncSession, payload: RegisterRequest) -> InstanceOut:
    """Registration is an upsert (3.2a): if an already known ``instance_id``
    registers again (e.g. after a reconnect), its data is updated instead of
    raising a conflict.
    """
    now = datetime.now(UTC)
    instance = await session.get(ServiceInstance, payload.instance_id)
    if instance is None:
        # Only a genuine new registration starts as "active" - re-registering
        # the same instance_id (self-healing after a 404, see
        # dms-registry-client) is not a restart and must not silently reset
        # a previously set "draining" status.
        instance = ServiceInstance(
            instance_id=payload.instance_id, registered_at=now, status="active"
        )
        session.add(instance)

    instance.service_type = payload.service_type
    instance.version = payload.version
    instance.capabilities = payload.capabilities
    instance.sensors = [sensor.model_dump() for sensor in payload.sensors]
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


async def mark_draining(session: AsyncSession, instance_id: str) -> InstanceOut:
    """Drain mechanism (10.5/3.8, P10-S2): only sets the status, does not
    terminate/abort the instance - see
    `gateway_service.upstream.InstanceResolver`, which excludes draining
    instances from the pool for NEW requests."""
    instance = await session.get(ServiceInstance, instance_id)
    if instance is None:
        raise InstanceNotFoundError(instance_id)
    instance.status = "draining"
    await session.flush()
    return _to_out(instance, timeout_seconds=0, now=datetime.now(UTC))


async def activate(session: AsyncSession, instance_id: str) -> InstanceOut:
    """Reversal of `mark_draining` (10.5, P10-S3): without this endpoint
    there would be no real rollback path - concept 10.5 explicitly requires
    that a rollback remains possible "as long as the drain... has not yet
    fully completed". Used e.g. by `scripts/rolling-update.sh` when a
    rollout is manually rolled back."""
    instance = await session.get(ServiceInstance, instance_id)
    if instance is None:
        raise InstanceNotFoundError(instance_id)
    instance.status = "active"
    await session.flush()
    return _to_out(instance, timeout_seconds=0, now=datetime.now(UTC))


async def deregister(session: AsyncSession, instance_id: str) -> InstanceOut:
    instance = await session.get(ServiceInstance, instance_id)
    if instance is None:
        raise InstanceNotFoundError(instance_id)
    result = _to_out(instance, timeout_seconds=0, now=datetime.now(UTC))
    await session.delete(instance)
    await session.flush()
    return result


async def list_active_by_type(
    session: AsyncSession, service_type: str, *, heartbeat_timeout_seconds: float
) -> list[InstanceOut]:
    """The active routing table (3.2a): only instances whose last heartbeat
    falls within the configured time window. No separate background sweep
    needed - failure is evaluated on read rather than via a mutating
    background job, which avoids race conditions.
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
