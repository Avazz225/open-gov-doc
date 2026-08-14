import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from federation_hub_service import repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.main import _run_retry_tick, app, settings


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _generate_keypair() -> tuple[bytes, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


def _signed_post(
    client, path: str, payload: dict, private_key_pem: bytes, *, installation_id: str | None = None
) -> httpx.Response:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Installation-Signature": sign_body(private_key_pem, body),
    }
    if installation_id is not None:
        headers["X-Installation-Id"] = installation_id
    return client.post(path, content=body, headers=headers)


def _make_stub_receiver() -> tuple[FastAPI, list[dict]]:
    received: list[dict] = []
    stub = FastAPI()

    @stub.post("/federation/inbound")
    async def inbound(request: Request) -> dict:
        received.append({"body": await request.body()})
        return {"status": "ok"}

    return stub, received


def _register(client, **overrides) -> tuple[dict, bytes]:
    private_pem, public_pem = _generate_keypair()
    payload = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://receiver.test",
        "public_key_pem": public_pem,
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    payload.update(overrides)
    response = _signed_post(client, "/installations", payload, private_pem)
    assert response.status_code == 201, response.text
    return response.json(), private_pem


async def test_run_retry_tick_redelivers_a_due_handover(client, session_factory):
    """Post-Roadmap Phase 20 Session 5 (ADR 0081): der Retry-Poll-Loop-Tick
    greift einen fälligen, im Hub-Prozessspeicher zwischengespeicherten
    Handover auf und stellt ihn erneut zu."""
    sender, sender_key = _register(client)
    target, _ = _register(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": base64.b64encode(b"opaque").decode(),
        }
        created = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        ).json()
        assert created["status"] == "pending_retry"
        assert created["attempts"] == 1
        assert created["id"] in app.state.pending_handover_payloads

        # next_retry_at liegt normalerweise in der (nahen) Zukunft - fuer
        # einen deterministischen Tick-Test direkt in die Vergangenheit gesetzt.
        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        await _run_retry_tick(session_factory, app.state.pending_handover_payloads)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivered"
            assert fresh.attempts == 1
        assert len(received) == 1
        assert created["id"] not in app.state.pending_handover_payloads
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_retry_tick_skips_handovers_not_yet_due(client, session_factory):
    sender, sender_key = _register(client)
    target, _ = _register(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        created = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        ).json()
        assert created["attempts"] == 1

        await _run_retry_tick(session_factory, app.state.pending_handover_payloads)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            # next_retry_at liegt noch in der Zukunft (Full-Jitter-Backoff nach
            # dem ersten Fehlschlag) - der Tick darf sie nicht anfassen.
            assert fresh.attempts == 1
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_retry_tick_keeps_cached_payload_after_reaching_delivery_failed(
    client, session_factory
):
    """Regressionstest für einen beim Entwerfen dieser Session gefundenen
    Bug: der Cache-Eintrag darf NICHT im selben Tick entfernt werden, der den
    Handover erst nach `delivery_failed` erschöpft - sonst wäre `POST
    .../retry` in der Praxis nie in der Lage, ihn zu nutzen (die 409-Gate
    verlangt `delivery_failed`, das aber genau in dem Moment eintritt, in dem
    der Cache sonst geleert würde). Nur ein ERFOLGREICHER Versuch entfernt
    den Eintrag (ADR 0081)."""
    sender, sender_key = _register(client)
    target, _ = _register(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 2
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        created = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        ).json()
        assert created["status"] == "pending_retry"
        assert created["attempts"] == 1

        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        await _run_retry_tick(session_factory, app.state.pending_handover_payloads)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivery_failed"
            assert fresh.attempts == 2
        assert created["id"] in app.state.pending_handover_payloads
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_retry_tick_marks_delivery_failed_when_payload_cache_lost(
    client, session_factory
):
    """Deckt die dokumentierte Grenze ab (ADR 0081, `models.Handover`-
    Docstring): geht der zwischengespeicherte Payload verloren (z. B. durch
    einen Neustart des Hub), kann der Tick nicht automatisch nachstellen und
    markiert den Handover stattdessen als endgültig fehlgeschlagen, statt
    endlos auf einen nie eintreffenden Payload zu warten."""
    sender, sender_key = _register(client)
    target, _ = _register(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        created = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        ).json()
        assert created["status"] == "pending_retry"

        # Simuliert den Verlust des Prozessspeicher-Cache (z. B. Neustart).
        app.state.pending_handover_payloads.pop(created["id"], None)

        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        await _run_retry_tick(session_factory, app.state.pending_handover_payloads)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivery_failed"
            assert fresh.next_retry_at is None
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts
