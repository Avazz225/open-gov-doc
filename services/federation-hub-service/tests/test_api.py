import base64
import uuid

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_installation_payload(**overrides) -> dict:
    payload = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://receiver.test",
        "public_key_pem": "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n",
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    payload.update(overrides)
    return payload


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "federation-hub-service"


def test_public_key_is_stable_across_requests(client):
    first = client.get("/public-key").json()["public_key_pem"]
    second = client.get("/public-key").json()["public_key_pem"]
    assert first == second
    assert "BEGIN PUBLIC KEY" in first


def test_register_then_update_requires_api_key(client):
    payload = make_installation_payload()

    register_response = client.post("/installations", json=payload)
    assert register_response.status_code == 201
    api_key = register_response.json()["api_key"]
    assert api_key

    unauthorized = client.post("/installations", json={**payload, "display_name": "Neu"})
    assert unauthorized.status_code == 401

    authorized = client.post(
        "/installations",
        json={**payload, "display_name": "Neu"},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert authorized.status_code == 201
    assert authorized.json()["api_key"] is None

    listed = client.get("/installations").json()
    entry = next(i for i in listed if i["id"] == payload["id"])
    assert entry["display_name"] == "Neu"
    assert "api_key_hash" not in entry


def test_deregister_requires_api_key(client):
    payload = make_installation_payload()
    api_key = client.post("/installations", json=payload).json()["api_key"]

    assert client.delete(f"/installations/{payload['id']}").status_code == 401
    response = client.delete(
        f"/installations/{payload['id']}", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert response.status_code == 204


def test_create_handover_rejects_incompatible_versions(client):
    sender = make_installation_payload(version="1.0", min_compatible_peer_version="1.0")
    target = make_installation_payload(version="0.5", min_compatible_peer_version="0.5")
    sender_key = client.post("/installations", json=sender).json()["api_key"]
    client.post("/installations", json=target)

    response = client.post(
        "/handovers",
        json={
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        },
        headers={"Authorization": f"Bearer {sender_key}"},
    )
    assert response.status_code == 409


def test_create_handover_requires_valid_api_key(client):
    target = make_installation_payload()
    client.post("/installations", json=target)

    response = client.post(
        "/handovers",
        json={
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        },
        headers={"Authorization": "Bearer does-not-exist"},
    )
    assert response.status_code == 401


def _make_stub_receiver() -> tuple[FastAPI, list[dict]]:
    """Ersatz für eine echte zweite Installation - verifiziert, dass die vom
    Hub signierte Zustellung tatsächlich mit dem über `/public-key` abrufbaren
    öffentlichen Schlüssel überprüfbar ist, und merkt sich empfangene Bodies."""
    received: list[dict] = []
    stub = FastAPI()

    @stub.post("/federation/inbound")
    async def inbound(request: Request) -> dict:
        body = await request.body()
        signature = request.headers.get("X-Federation-Hub-Signature", "")
        received.append({"body": body, "signature": signature})
        return {"status": "ok"}

    @stub.post("/federation/inbound-result")
    async def inbound_result(request: Request) -> dict:
        body = await request.body()
        signature = request.headers.get("X-Federation-Hub-Signature", "")
        received.append({"body": body, "signature": signature})
        return {"status": "ok"}

    return stub, received


def test_create_handover_delivers_signed_payload_to_target_callback(client):
    sender = make_installation_payload()
    target = make_installation_payload(callback_base_url="http://receiver.test")
    sender_key = client.post("/installations", json=sender).json()["api_key"]
    client.post("/installations", json=target)

    stub, received = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

    response = client.post(
        "/handovers",
        json={
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": base64.b64encode(b"opaque-ciphertext").decode(),
        },
        headers={"Authorization": f"Bearer {sender_key}"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "delivered"
    assert len(received) == 1

    public_key_pem = client.get("/public-key").json()["public_key_pem"].encode()
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(public_key_pem)
    public_key.verify(
        base64.b64decode(received[0]["signature"]),
        received[0]["body"],
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )

    status_response = client.get(f"/handovers/{body['id']}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "delivered"


def test_create_handover_marks_delivery_failed_on_unreachable_target(client):
    sender = make_installation_payload()
    target = make_installation_payload(callback_base_url="http://unreachable.invalid")
    sender_key = client.post("/installations", json=sender).json()["api_key"]
    client.post("/installations", json=target)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    response = client.post(
        "/handovers",
        json={
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        },
        headers={"Authorization": f"Bearer {sender_key}"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "delivery_failed"


def test_submit_result_only_allowed_by_target_installation(client):
    sender = make_installation_payload()
    target = make_installation_payload()
    sender_key = client.post("/installations", json=sender).json()["api_key"]
    target_key = client.post("/installations", json=target).json()["api_key"]

    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

    handover = client.post(
        "/handovers",
        json={
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        },
        headers={"Authorization": f"Bearer {sender_key}"},
    ).json()

    wrong_caller = client.post(
        f"/handovers/{handover['id']}/result",
        json={"outcome": "completed", "encrypted_result": "opaque-result"},
        headers={"Authorization": f"Bearer {sender_key}"},
    )
    assert wrong_caller.status_code == 403

    correct_caller = client.post(
        f"/handovers/{handover['id']}/result",
        json={"outcome": "completed", "encrypted_result": "opaque-result"},
        headers={"Authorization": f"Bearer {target_key}"},
    )
    assert correct_caller.status_code == 200
    assert correct_caller.json()["status"] == "completed"


def test_get_handover_unknown_returns_404(client):
    assert client.get("/handovers/does-not-exist").status_code == 404


def test_sign_body_signature_is_verifiable_helper_smoke():
    """Reiner Sanity-Check der Krypto-Hilfsfunktion selbst, unabhängig vom
    HTTP-Rundlauf oben."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    body = b'{"hello":"world"}'
    signature_b64 = sign_body(private_pem, body)

    key.public_key().verify(
        base64.b64decode(signature_b64),
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
