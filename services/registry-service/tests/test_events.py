import asyncio
import os
import uuid

import pytest
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient
from registry_service.main import app, run_replica_tick
from registry_service.main import settings as registry_settings
from sqlalchemy import text

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture
def client():
    """Default `X-DMS-Principal` matching the `service_type` both tests
    below register as (Phase 59 Session 4) - see `test_api.py::client`."""
    with TestClient(app, headers={"X-DMS-Principal": "document-service"}) as c:
        yield c


@pytest.fixture
async def consumer():
    bus = NatsEventBusClient(NATS_URL, ensure_stream=False)
    await bus.connect()
    yield bus
    await bus.close()


async def test_register_publishes_event(client, consumer):
    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        "registry.instance.registered",
        handler,
        durable=f"test-{uuid.uuid4().hex[:8]}",
        deliver_new=True,
    )
    await asyncio.sleep(0.2)

    instance_id = f"test-{uuid.uuid4().hex[:8]}"
    client.post(
        "/instances",
        json={
            "instance_id": instance_id,
            "service_type": "document-service",
            "version": "0.1.0",
            "capabilities": [],
            "health_endpoint": "http://doc-1:8000/healthz",
            "address": "http://doc-1:8000",
        },
    )

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].subject == instance_id
    assert received[0].service_name == "registry-service"
    assert received[0].payload["service_type"] == "document-service"


async def test_deregister_publishes_event(client, consumer):
    instance_id = f"test-{uuid.uuid4().hex[:8]}"
    client.post(
        "/instances",
        json={
            "instance_id": instance_id,
            "service_type": "document-service",
            "version": "0.1.0",
            "capabilities": [],
            "health_endpoint": "http://doc-1:8000/healthz",
            "address": "http://doc-1:8000",
        },
    )

    received: list[Event] = []
    got_message = asyncio.Event()

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))
        got_message.set()

    await consumer.subscribe(
        "registry.instance.deregistered",
        handler,
        durable=f"test-{uuid.uuid4().hex[:8]}",
        deliver_new=True,
    )
    await asyncio.sleep(0.2)

    client.delete(f"/instances/{instance_id}")

    await asyncio.wait_for(got_message.wait(), timeout=5)
    assert received[0].subject == instance_id


# --- Replica loop (P87-S2, ADR 0241) -----------------------------------------


def _register(client, service_type: str) -> str:
    instance_id = f"test-{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/instances",
        json={
            "instance_id": instance_id,
            "service_type": service_type,
            "version": "0.1.0",
            "capabilities": [],
            "health_endpoint": "http://x:8000/healthz",
            "address": "http://x:8000",
        },
        headers={"X-DMS-Principal": service_type},
    )
    assert response.status_code == 201
    return instance_id


async def _collect(consumer, subjects: list[str]) -> list[Event]:
    received: list[Event] = []

    async def handler(payload: bytes) -> None:
        received.append(Event.from_bytes(payload))

    for subject in subjects:
        await consumer.subscribe(
            subject, handler, durable=f"test-{uuid.uuid4().hex[:8]}", deliver_new=True
        )
    await asyncio.sleep(0.2)
    return received


async def _wait_for(received: list[Event], predicate) -> Event:
    async with asyncio.timeout(5):
        while True:
            match = next((e for e in received if predicate(e)), None)
            if match is not None:
                return match
            await asyncio.sleep(0.05)


async def test_tick_forgets_a_restarted_instance_with_reason_replaced(client, consumer, engine):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    old_id = _register(client, service_type)
    _register(client, service_type)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE registry.service_instance "
                "SET last_heartbeat_at = now() - interval '10 minutes' WHERE instance_id = :id"
            ),
            {"id": old_id},
        )
    received = await _collect(consumer, ["registry.instance.forgotten"])

    result = client.portal.call(run_replica_tick, app.state.session_factory)

    assert (old_id, service_type) in result["forgotten"]
    event = await _wait_for(received, lambda e: e.subject == old_id)
    assert event.payload == {"service_type": service_type, "reason": "replaced"}
    assert event.actor == "system:registry"


async def test_tick_alarms_once_on_under_replication_and_reports_restored(
    client, consumer, monitoring_admin_headers
):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    client.put(
        f"/replicas/{service_type}",
        json={"min_replicas": 2, "max_replicas": 3},
        headers=monitoring_admin_headers,
    )
    _register(client, service_type)
    received = await _collect(consumer, ["registry.replicas.under", "registry.replicas.restored"])
    registry_settings.replica_alarm_after_seconds = 0
    try:
        client.portal.call(run_replica_tick, app.state.session_factory)  # pending
        client.portal.call(run_replica_tick, app.state.session_factory)  # committed
        under = await _wait_for(received, lambda e: e.subject == service_type)
        assert under.event_type == "registry.replicas.under"
        assert under.payload["reachable"] == 1
        assert (under.payload["min"], under.payload["max"]) == (2, 3)

        client.portal.call(run_replica_tick, app.state.session_factory)
        assert len([e for e in received if e.subject == service_type]) == 1

        _register(client, service_type)
        client.portal.call(run_replica_tick, app.state.session_factory)
        client.portal.call(run_replica_tick, app.state.session_factory)
        restored = await _wait_for(
            received,
            lambda e: e.subject == service_type and e.event_type == "registry.replicas.restored",
        )
        assert restored.payload["previous_state"] == "under"
    finally:
        registry_settings.replica_alarm_after_seconds = 120.0
