"""Dead letters: list, inspect, replay, discard (P99-S2, ADR 0270)."""

import uuid
from unittest.mock import AsyncMock

import pytest
from dms_eventbus_client.nats_backend import dead_letter_subject, replay_subject
from fastapi.testclient import TestClient
from monitoring_service.main import app

ADMIN = {"x-dms-principal": "alice"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.list_active_instances.return_value = []
        yield c


@pytest.fixture
def letter(client):
    """One dead letter for a stream of its own; cleaned up afterwards."""
    stream = f"teststream{uuid.uuid4().hex[:6]}"
    bus = app.state.event_bus

    async def put():
        await bus._ensure_dlq()
        await bus._js.publish(
            dead_letter_subject(stream, "probe-consumer"),
            b'{"event_type": "folder.resource.moved", "payload": {"resource_id": "x"}}',
            headers={
                "Dms-Dlq-Subject": f"{stream}.resource.moved",
                "Dms-Dlq-Stream": stream,
                "Dms-Dlq-Stream-Seq": "41",
                "Dms-Dlq-Consumer": "probe-consumer",
                "Dms-Dlq-Deliveries": "12",
                "Dms-Dlq-Error": "IntegrityError: parent missing",
                "Dms-Dlq-Failed-At": "2026-11-27T12:00:00+00:00",
            },
        )

    async def cleanup():
        await bus._js.purge_stream("DLQ", subject=f"dlq.{stream}.>")
        try:
            await bus._js.purge_stream("REPLAY", subject=f"replay.{stream}.>")
        except Exception:  # noqa: BLE001 - no replay stream yet
            pass

    client.portal.call(put)
    yield stream
    client.portal.call(cleanup)


def _mine(client, stream):
    return [x for x in client.get("/dead-letters", headers=ADMIN).json() if x["stream"] == stream]


def test_list_and_detail(client, letter):
    [item] = _mine(client, letter)
    assert item["consumer"] == "probe-consumer"
    assert item["subject"] == f"{letter}.resource.moved"
    assert item["deliveries"] == 12 and item["error"].startswith("IntegrityError")
    detail = client.get(f"/dead-letters/{item['seq']}", headers=ADMIN).json()
    assert '"folder.resource.moved"' in detail["payload"]
    assert detail["payload_truncated"] is False
    assert client.get("/dead-letters/999999999", headers=ADMIN).status_code == 404


def test_replay_moves_it_to_the_consumers_replay_channel(client, letter):
    [item] = _mine(client, letter)
    response = client.post(
        f"/dead-letters/{item['seq']}/replay", json={"reason": "Ordner angelegt"}, headers=ADMIN
    )
    assert response.status_code == 202
    assert _mine(client, letter) == []
    bus = app.state.event_bus

    async def replayed():
        info = await bus._js.stream_info("REPLAY", subjects_filter="replay.>")
        return (info.state.subjects or {}).get(replay_subject(letter, "probe-consumer"), 0)

    assert client.portal.call(replayed) == 1


def test_discard_needs_a_reason_and_removes_it(client, letter):
    [item] = _mine(client, letter)
    assert client.delete(f"/dead-letters/{item['seq']}", headers=ADMIN).status_code == 422
    response = client.delete(
        f"/dead-letters/{item['seq']}", params={"reason": "Test-Altlast"}, headers=ADMIN
    )
    assert response.status_code == 200
    assert _mine(client, letter) == []
    again = client.delete(
        f"/dead-letters/{item['seq']}", params={"reason": "nochmal"}, headers=ADMIN
    )
    assert again.status_code == 404


def test_everything_needs_the_monitoring_role(client, letter):
    app.state.permission_client.has_permission.return_value = False
    seq = 1  # gated before the letter is even looked up
    assert client.get("/dead-letters", headers=ADMIN).status_code == 403
    assert client.get(f"/dead-letters/{seq}", headers=ADMIN).status_code == 403
    assert (
        client.post(
            f"/dead-letters/{seq}/replay", json={"reason": "x" * 3}, headers=ADMIN
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/dead-letters/{seq}", params={"reason": "xyz"}, headers=ADMIN).status_code
        == 403
    )
