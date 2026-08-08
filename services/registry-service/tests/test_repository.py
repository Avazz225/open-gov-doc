import uuid
from datetime import UTC, datetime, timedelta

import pytest
from registry_service import repository
from registry_service.models import ServiceInstance
from registry_service.schemas import RegisterRequest


def make_request(**overrides) -> RegisterRequest:
    defaults = dict(
        instance_id=f"test-{uuid.uuid4().hex[:8]}",
        service_type="document-service",
        version="0.1.0",
        capabilities=["read", "write"],
        health_endpoint="http://doc-1:8000/healthz",
        address="http://doc-1:8000",
    )
    defaults.update(overrides)
    return RegisterRequest(**defaults)


async def test_register_creates_instance(session):
    req = make_request()
    result = await repository.register(session, req)

    assert result.instance_id == req.instance_id
    assert result.healthy is True
    assert result.capabilities == ["read", "write"]
    assert result.sensors == []


async def test_register_persists_sensor_declarations(session):
    req = make_request(
        sensors=[
            {
                "name": "document.upload.duration",
                "group": "performance",
                "cost": "expensive",
                "description": "Dauer eines Uploads",
            }
        ]
    )
    result = await repository.register(session, req)

    assert len(result.sensors) == 1
    assert result.sensors[0].name == "document.upload.duration"


async def test_register_is_idempotent_upsert(session):
    req = make_request()
    await repository.register(session, req)

    updated = make_request(instance_id=req.instance_id, version="0.2.0")
    result = await repository.register(session, updated)

    assert result.version == "0.2.0"
    count = (await session.get(ServiceInstance, req.instance_id)) is not None
    assert count


async def test_heartbeat_updates_timestamp(session):
    req = make_request()
    await repository.register(session, req)

    result = await repository.heartbeat(session, req.instance_id)

    assert result.instance_id == req.instance_id


async def test_heartbeat_unknown_instance_raises(session):
    with pytest.raises(repository.InstanceNotFoundError):
        await repository.heartbeat(session, "does-not-exist")


async def test_mark_draining_sets_status(session):
    req = make_request()
    await repository.register(session, req)

    result = await repository.mark_draining(session, req.instance_id)

    assert result.status == "draining"
    instance = await session.get(ServiceInstance, req.instance_id)
    assert instance.status == "draining"


async def test_mark_draining_unknown_instance_raises(session):
    with pytest.raises(repository.InstanceNotFoundError):
        await repository.mark_draining(session, "does-not-exist")


async def test_heartbeat_does_not_reset_draining(session):
    req = make_request()
    await repository.register(session, req)
    await repository.mark_draining(session, req.instance_id)

    result = await repository.heartbeat(session, req.instance_id)

    assert result.status == "draining"


async def test_activate_resets_status_to_active(session):
    req = make_request()
    await repository.register(session, req)
    await repository.mark_draining(session, req.instance_id)

    result = await repository.activate(session, req.instance_id)

    assert result.status == "active"
    instance = await session.get(ServiceInstance, req.instance_id)
    assert instance.status == "active"


async def test_activate_unknown_instance_raises(session):
    with pytest.raises(repository.InstanceNotFoundError):
        await repository.activate(session, "does-not-exist")


async def test_deregister_removes_instance(session):
    req = make_request()
    await repository.register(session, req)

    result = await repository.deregister(session, req.instance_id)

    assert result.service_type == req.service_type
    assert await session.get(ServiceInstance, req.instance_id) is None


async def test_deregister_unknown_instance_raises(session):
    with pytest.raises(repository.InstanceNotFoundError):
        await repository.deregister(session, "does-not-exist")


async def test_list_active_by_type_excludes_stale_instances(session):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    fresh = make_request(service_type=service_type)
    stale = make_request(service_type=service_type)
    await repository.register(session, fresh)
    await repository.register(session, stale)

    stale_instance = await session.get(ServiceInstance, stale.instance_id)
    stale_instance.last_heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    active = await repository.list_active_by_type(
        session, service_type, heartbeat_timeout_seconds=15.0
    )

    active_ids = {i.instance_id for i in active}
    assert fresh.instance_id in active_ids
    assert stale.instance_id not in active_ids


async def test_list_all_includes_stale_with_healthy_flag(session):
    req = make_request()
    await repository.register(session, req)
    instance = await session.get(ServiceInstance, req.instance_id)
    instance.last_heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    all_instances = await repository.list_all(session, heartbeat_timeout_seconds=15.0)

    match = next(i for i in all_instances if i.instance_id == req.instance_id)
    assert match.healthy is False
