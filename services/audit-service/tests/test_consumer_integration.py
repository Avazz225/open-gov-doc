import asyncio
import os
import uuid

import pytest
from audit_service.main import app
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi.testclient import TestClient

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


async def test_published_registry_event_is_consumed_and_recorded(client):
    """End-to-End: ein auf 'registry.>' publiziertes Event landet unveränderlich
    und kettenkonform im Audit Service - genau der Ablauf aus Konzept 3.4/5.3.
    """
    producer = NatsEventBusClient(NATS_URL, stream="registry")
    await producer.connect()
    try:
        event = Event(
            event_type="registry.instance.registered",
            service_name="registry-service",
            subject=f"doc-{uuid.uuid4().hex[:8]}",
            payload={"service_type": "document-service"},
        )
        await producer.publish(event.event_type, event.to_bytes())

        for _ in range(50):
            response = client.get("/events", params={"limit": 1000})
            if any(e["event_id"] == event.event_id for e in response.json()):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Event wurde nicht innerhalb des Timeouts konsumiert")

        verify = client.get("/events/verify")
        assert verify.json()["ok"] is True
    finally:
        await producer.close()
