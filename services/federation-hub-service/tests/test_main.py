import asyncio
import base64
import json
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dms_db_base import build_engine, make_session_factory
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from federation_hub_service import repository
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.main import (
    _handover_retry_poll_loop,
    _run_result_retry_tick,
    _run_retry_tick,
    app,
    settings,
)


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


async def _has_pending_payload(session_factory, handover_id: str, *, leg: str) -> bool:
    """Post-Roadmap Phase 73 Session 3 (ADR 0213): replaces the old
    `handover_id in app.state.pending_handover_payloads`/`..._result_
    payloads` membership checks - queries through the test's own
    `session_factory` fixture, a separate engine/connection from
    `app.state.session_factory`."""
    async with session_factory() as session:
        return await repository.get_pending_payload(session, handover_id, leg=leg) is not None


async def _drop_pending_payload(session_factory, handover_id: str, *, leg: str) -> None:
    """Simulates loss of the cached payload for the defensive-fallback
    tests below (`cached is None` in `_run_retry_tick`/`_run_result_retry_
    tick`) - replaces `app.state.pending_handover_payloads.pop(...)`/
    `..._result_payloads.pop(...)`."""
    async with session_factory() as session:
        await repository.delete_pending_payload(session, handover_id, leg=leg)
        await session.commit()


def _make_stub_receiver() -> tuple[FastAPI, list[dict]]:
    received: list[dict] = []
    stub = FastAPI()

    @stub.post("/federation/inbound")
    async def inbound(request: Request) -> dict:
        received.append({"body": await request.body()})
        return {"status": "ok"}

    @stub.post("/federation/inbound-result")
    async def inbound_result(request: Request) -> dict:
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
        assert await _has_pending_payload(session_factory, created["id"], leg="forward")

        # next_retry_at liegt normalerweise in der (nahen) Zukunft - fuer
        # einen deterministischen Tick-Test direkt in die Vergangenheit gesetzt.
        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        await _run_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivered"
            assert fresh.attempts == 1
        assert len(received) == 1
        assert not await _has_pending_payload(session_factory, created["id"], leg="forward")
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_handover_retry_poll_loop_skips_tick_while_maintenance_active(
    client, session_factory
):
    """Post-Roadmap Phase 44 Session 3 (4.8, ADR 0164) - proves the new
    maintenance-mode guard actually prevents redelivery, not just that it
    compiles: same due-handover setup as `test_run_retry_tick_redelivers_
    a_due_handover` above, but driven through the real poll loop (not the
    factored-out tick function directly) with a fake `permission_client`
    reporting maintenance mode active. Runs the loop as a background task
    for a couple of shortened intervals, then cancels it - the same
    approach every OTHER Category B rollout site in this ADR's rollout
    shares, but this is the only one of the ~9 with its own dedicated
    live test, since it's also this service's first-ever `permission-
    service` integration and the one non-trivial design decision (the
    `permission_client` parameter being optional/`None`-able) in this
    session's rollout."""
    sender, sender_key = _register(client)
    target, _ = _register(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    original_interval = settings.handover_retry_poll_interval_seconds
    settings.max_handover_delivery_attempts = 5
    settings.handover_retry_poll_interval_seconds = 0.05
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

        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        fake_permission_client = AsyncMock()
        fake_permission_client.is_maintenance_active.return_value = True

        loop_task = asyncio.create_task(
            _handover_retry_poll_loop(
                session_factory,
                fake_permission_client,
            )
        )
        try:
            await asyncio.sleep(0.3)
        finally:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task

        fake_permission_client.is_maintenance_active.assert_awaited()
        assert len(received) == 0
        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "pending_retry"
        assert await _has_pending_payload(session_factory, created["id"], leg="forward")
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts
        settings.handover_retry_poll_interval_seconds = original_interval


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

        await _run_retry_tick(session_factory)

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

        await _run_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivery_failed"
            assert fresh.attempts == 2
        assert await _has_pending_payload(session_factory, created["id"], leg="forward")
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

        # Simuliert eine fehlende/beschädigte Zeile (ADR 0213 "Rationale" -
        # der defensive Fallback bleibt bestehen, auch wenn ein normaler
        # Neustart dafür seit ADR 0213 nicht mehr die Ursache sein kann).
        await _drop_pending_payload(session_factory, created["id"], leg="forward")

        async with session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        await _run_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivery_failed"
            assert fresh.next_retry_at is None
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def _create_delivered_handover(client, session_factory, *, sender, sender_key, target):
    """Erzeugt einen bereits erfolgreich zugestellten Handover - Vorbedingung
    für alle Result-Retry-Tick-Tests unten (`submit_handover_result` läuft
    unabhängig vom Zustellstatus, aber ein realistisches Szenario braucht
    einen bereits zugestellten Handover, bevor die Zielinstallation ein
    Ergebnis zurückmeldet)."""
    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))
    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    created = _signed_post(
        client, "/handovers", payload, sender_key, installation_id=sender["id"]
    ).json()
    assert created["status"] == "delivered"
    return created


async def test_run_result_retry_tick_redelivers_a_due_handover(client, session_factory):
    """Return-path counterpart of `test_run_retry_tick_redelivers_a_due_
    handover` (Phase 40 Session 3) - the poll-loop tick redelivers a due,
    process-memory-cached result to the ORIGIN installation."""
    sender, sender_key = _register(client, callback_base_url="http://unreachable.invalid")
    target, target_key = _register(client)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        handover = await _create_delivered_handover(
            client, session_factory, sender=sender, sender_key=sender_key, target=target
        )

        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        result = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert result["status"] == "result_pending_retry"
        assert result["result_attempts"] == 1
        assert await _has_pending_payload(session_factory, handover["id"], leg="result")

        async with session_factory() as session:
            fresh = await repository.get_handover(session, handover["id"])
            fresh.result_next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        await _run_result_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, handover["id"])
            assert fresh.status == "completed"
            assert fresh.result_attempts == 1
        assert len(received) == 1
        assert not await _has_pending_payload(session_factory, handover["id"], leg="result")
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_result_retry_tick_skips_handovers_not_yet_due(client, session_factory):
    sender, sender_key = _register(client, callback_base_url="http://unreachable.invalid")
    target, target_key = _register(client)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        handover = await _create_delivered_handover(
            client, session_factory, sender=sender, sender_key=sender_key, target=target
        )

        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        result = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert result["result_attempts"] == 1

        await _run_result_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, handover["id"])
            # result_next_retry_at liegt noch in der Zukunft - der Tick darf
            # sie nicht anfassen.
            assert fresh.result_attempts == 1
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_result_retry_tick_keeps_cached_payload_after_reaching_result_delivery_failed(
    client, session_factory
):
    sender, sender_key = _register(client, callback_base_url="http://unreachable.invalid")
    target, target_key = _register(client)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 2
    try:
        handover = await _create_delivered_handover(
            client, session_factory, sender=sender, sender_key=sender_key, target=target
        )

        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        result = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert result["status"] == "result_pending_retry"
        assert result["result_attempts"] == 1

        async with session_factory() as session:
            fresh = await repository.get_handover(session, handover["id"])
            fresh.result_next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        await _run_result_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, handover["id"])
            assert fresh.status == "result_delivery_failed"
            assert fresh.result_attempts == 2
        assert await _has_pending_payload(session_factory, handover["id"], leg="result")
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


async def test_run_result_retry_tick_marks_result_delivery_failed_when_payload_cache_lost(
    client, session_factory
):
    sender, sender_key = _register(client, callback_base_url="http://unreachable.invalid")
    target, target_key = _register(client)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        handover = await _create_delivered_handover(
            client, session_factory, sender=sender, sender_key=sender_key, target=target
        )

        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        result = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert result["status"] == "result_pending_retry"

        await _drop_pending_payload(session_factory, handover["id"], leg="result")

        async with session_factory() as session:
            fresh = await repository.get_handover(session, handover["id"])
            fresh.result_next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        await _run_result_retry_tick(session_factory)

        async with session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, handover["id"])
            assert fresh.status == "result_delivery_failed"
            assert fresh.result_next_retry_at is None
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts


# --- Restart-survival proof (Post-Roadmap Phase 73 Session 3, ADR 0213) ---


async def test_run_retry_tick_survives_a_simulated_hub_restart(client):
    """The literal claim ADR 0213 makes, proven end to end rather than just
    asserted against the repository functions in isolation: `create_handover`
    persists the payload via `app.state.session_factory` (the session the
    running app itself used to handle the `POST /handovers` request); this
    test then drives the ENTIRE automatic-redelivery tick through a
    COMPLETELY SEPARATE engine/connection pool it builds itself right here
    (its own `build_engine(...)`/`make_session_factory(...)` call, sharing
    no Python object whatsoever with `app.state.engine`) - `_run_retry_tick`
    never touches `app.state.session_factory` at all in this test. That is
    exactly what "hub restarted" looks like from the payload's perspective:
    before ADR 0213, the payload existed only as an entry in a Python dict
    tied to the one process that wrote it - `_run_retry_tick` here would
    have logged `federation_handover_retry_payload_lost` and given up
    (`cached is None`) the moment it ran anywhere but that exact process.
    After ADR 0213, only the DB row matters, so a totally independent
    engine succeeds just the same."""
    fresh_engine = build_engine(settings.postgres_dsn)
    fresh_session_factory = make_session_factory(fresh_engine)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = settings.max_handover_delivery_attempts
    settings.max_handover_delivery_attempts = 5
    try:
        sender, sender_key = _register(client)
        target, _ = _register(client, callback_base_url="http://unreachable.invalid")
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

        async with fresh_session_factory() as session:
            handover = await repository.get_handover(session, created["id"])
            handover.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        # Driven ENTIRELY through the fresh engine built above - proves
        # `_run_retry_tick` needs nothing from `app.state` to redeliver.
        await _run_retry_tick(fresh_session_factory)

        async with fresh_session_factory() as fresh_session:
            fresh = await repository.get_handover(fresh_session, created["id"])
            assert fresh.status == "delivered"
        assert len(received) == 1
    finally:
        settings.max_handover_delivery_attempts = original_max_attempts
        await fresh_engine.dispose()
