import base64
import json
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from federation_hub_service.crypto_utils import sign_body
from federation_hub_service.main import app
from federation_hub_service.main import settings as hub_settings


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


def make_installation_payload(*, public_key_pem: str, **overrides) -> dict:
    payload = {
        "id": f"install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Installation",
        "callback_base_url": "http://receiver.test",
        "public_key_pem": public_key_pem,
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    payload.update(overrides)
    return payload


def _signed_post(
    client, path: str, payload: dict, private_key_pem: bytes, *, installation_id: str | None = None
) -> httpx.Response:
    """P13-S4/ADR 0039: signiert exakt die Bytes, die auch übertragen werden
    (`content=body`, nicht `json=payload`) - sonst könnten `httpx`s eigene
    JSON-Serialisierung und die hier signierten Bytes auseinanderlaufen."""
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Installation-Signature": sign_body(private_key_pem, body),
    }
    if installation_id is not None:
        headers["X-Installation-Id"] = installation_id
    return client.post(path, content=body, headers=headers)


def register_installation(client, **overrides) -> tuple[dict, bytes]:
    """Erzeugt ein frisches Schlüsselpaar, registriert damit eine neue
    Test-Installation (signiert mit dem gerade erzeugten privaten Schlüssel -
    Selbstkonsistenz-Nachweis, siehe `repository.register_or_update_
    installation`) und gibt ``(Installation-Dict, privater Schlüssel)`` zurück."""
    private_pem, public_pem = _generate_keypair()
    payload = make_installation_payload(public_key_pem=public_pem, **overrides)
    response = _signed_post(client, "/installations", payload, private_pem)
    assert response.status_code == 201, response.text
    return response.json(), private_pem


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "federation-hub-service"


def test_public_key_is_stable_across_requests(client):
    first = client.get("/public-key").json()["public_key_pem"]
    second = client.get("/public-key").json()["public_key_pem"]
    assert first == second
    assert "BEGIN PUBLIC KEY" in first


def test_register_with_non_numeric_version_returns_422(client):
    """P13-S3-Fund: vor dieser Validierung wurde ein nicht-numerischer
    `version`-String klaglos gespeichert und ließ erst eine spätere, völlig
    andere `POST /handovers`-Vermittlung mit einem unbehandelten `ValueError`
    (500) abstürzen, siehe `version_utils.py`. Bewusst unsigniert - die
    Format-Validierung muss schon vor jeder Signaturprüfung greifen (siehe
    `main._parse_body`)."""
    _, public_pem = _generate_keypair()
    response = client.post(
        "/installations",
        json=make_installation_payload(public_key_pem=public_pem, version="abc.def"),
    )
    assert response.status_code == 422


def test_register_with_non_numeric_min_compatible_peer_version_returns_422(client):
    _, public_pem = _generate_keypair()
    response = client.post(
        "/installations",
        json=make_installation_payload(
            public_key_pem=public_pem, min_compatible_peer_version="not-a-version"
        ),
    )
    assert response.status_code == 422


def test_register_rejects_signature_not_matching_submitted_public_key(client):
    """P13-S4/ADR 0039: Selbstkonsistenz-Nachweis bei der Neuanlage - wer sich
    registriert, muss den zum eingereichten `public_key_pem` passenden
    privaten Schlüssel tatsächlich besitzen."""
    _, public_pem = _generate_keypair()
    other_private_pem, _ = _generate_keypair()
    payload = make_installation_payload(public_key_pem=public_pem)
    response = _signed_post(client, "/installations", payload, other_private_pem)
    assert response.status_code == 401


def test_register_then_update_requires_matching_signature(client):
    installation, private_pem = register_installation(client)
    other_private_pem, _ = _generate_keypair()

    payload = make_installation_payload(
        id=installation["id"], public_key_pem=installation["public_key_pem"], display_name="Neu"
    )
    unauthorized = _signed_post(client, "/installations", payload, other_private_pem)
    assert unauthorized.status_code == 401

    authorized = _signed_post(client, "/installations", payload, private_pem)
    assert authorized.status_code == 201
    assert authorized.json()["display_name"] == "Neu"

    listed = client.get("/installations").json()
    entry = next(i for i in listed if i["id"] == installation["id"])
    assert entry["display_name"] == "Neu"


def test_reregister_ignores_submitted_public_key_change(client):
    """Ein Schlüsselwechsel läuft ausschließlich über `rotate-key` (ADR 0039)
    - eine reguläre Re-Registrierung mit einem abweichenden `public_key_pem`
    im Payload lässt den gespeicherten Schlüssel unverändert."""
    installation, private_pem = register_installation(client)
    _, other_public_pem = _generate_keypair()

    payload = make_installation_payload(id=installation["id"], public_key_pem=other_public_pem)
    response = _signed_post(client, "/installations", payload, private_pem)
    assert response.status_code == 201
    assert response.json()["public_key_pem"] == installation["public_key_pem"]


def test_deregister_requires_matching_signature(client):
    installation, private_pem = register_installation(client)

    unauthorized = client.delete(f"/installations/{installation['id']}")
    assert unauthorized.status_code == 401

    wrong_signature = sign_body(private_pem, b"not-the-installation-id")
    wrong = client.delete(
        f"/installations/{installation['id']}",
        headers={"X-Installation-Signature": wrong_signature},
    )
    assert wrong.status_code == 401

    correct_signature = sign_body(private_pem, installation["id"].encode("utf-8"))
    response = client.delete(
        f"/installations/{installation['id']}",
        headers={"X-Installation-Signature": correct_signature},
    )
    assert response.status_code == 204


def test_rotate_key_requires_signature_from_current_key(client):
    installation, private_pem = register_installation(client)
    _, new_public_pem = _generate_keypair()
    wrong_private_pem, _ = _generate_keypair()

    wrong_attempt = _signed_post(
        client,
        f"/installations/{installation['id']}/rotate-key",
        {"new_public_key_pem": new_public_pem},
        wrong_private_pem,
    )
    assert wrong_attempt.status_code == 401

    response = _signed_post(
        client,
        f"/installations/{installation['id']}/rotate-key",
        {"new_public_key_pem": new_public_pem},
        private_pem,
    )
    assert response.status_code == 200
    assert response.json()["public_key_pem"] == new_public_pem


def test_rotate_key_then_old_key_no_longer_authorizes(client):
    installation, private_pem = register_installation(client)
    new_private_pem, new_public_pem = _generate_keypair()

    rotate_response = _signed_post(
        client,
        f"/installations/{installation['id']}/rotate-key",
        {"new_public_key_pem": new_public_pem},
        private_pem,
    )
    assert rotate_response.status_code == 200

    payload = make_installation_payload(
        id=installation["id"], public_key_pem=new_public_pem, display_name="Nach Rotation"
    )
    stale_attempt = _signed_post(client, "/installations", payload, private_pem)
    assert stale_attempt.status_code == 401

    fresh_attempt = _signed_post(client, "/installations", payload, new_private_pem)
    assert fresh_attempt.status_code == 201
    assert fresh_attempt.json()["display_name"] == "Nach Rotation"


def test_revoke_requires_hub_operator_key(client):
    installation, _ = register_installation(client)
    response = client.post(f"/installations/{installation['id']}/revoke", json={"reason": "x"})
    assert response.status_code == 403


def test_revoke_with_operator_key_blocks_further_registration(client):
    hub_settings.hub_operator_key = "operator-secret-for-test"
    try:
        installation, private_pem = register_installation(client)
        revoke_response = client.post(
            f"/installations/{installation['id']}/revoke",
            json={"reason": "Schlüssel kompromittiert"},
            headers={"Authorization": "Bearer operator-secret-for-test"},
        )
        assert revoke_response.status_code == 200
        assert revoke_response.json()["revoked_at"] is not None
        assert revoke_response.json()["revoked_reason"] == "Schlüssel kompromittiert"

        blocked = _signed_post(
            client,
            "/installations",
            make_installation_payload(
                id=installation["id"], public_key_pem=installation["public_key_pem"]
            ),
            private_pem,
        )
        assert blocked.status_code == 401
    finally:
        hub_settings.hub_operator_key = None


def test_revoke_with_wrong_operator_key_returns_403(client):
    hub_settings.hub_operator_key = "operator-secret-for-test"
    try:
        installation, _ = register_installation(client)
        response = client.post(
            f"/installations/{installation['id']}/revoke",
            json={"reason": "x"},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert response.status_code == 403
    finally:
        hub_settings.hub_operator_key = None


def test_create_handover_rejects_incompatible_versions(client):
    sender, sender_key = register_installation(
        client, version="1.0", min_compatible_peer_version="1.0"
    )
    target, _ = register_installation(client, version="0.5", min_compatible_peer_version="0.5")

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])
    assert response.status_code == 409


def test_create_handover_requires_valid_signature(client):
    target, _ = register_installation(client)
    wrong_private_pem, _ = _generate_keypair()

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(
        client, "/handovers", payload, wrong_private_pem, installation_id="does-not-exist"
    )
    assert response.status_code == 401


def test_create_handover_rejects_revoked_target(client):
    hub_settings.hub_operator_key = "operator-secret-for-test"
    try:
        sender, sender_key = register_installation(client)
        target, _ = register_installation(client)
        client.post(
            f"/installations/{target['id']}/revoke",
            json={"reason": "x"},
            headers={"Authorization": "Bearer operator-secret-for-test"},
        )

        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        response = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        )
        assert response.status_code == 409
    finally:
        hub_settings.hub_operator_key = None


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
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://receiver.test")

    stub, received = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": base64.b64encode(b"opaque-ciphertext").decode(),
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "delivered"
    assert len(received) == 1

    public_key_pem = client.get("/public-key").json()["public_key_pem"].encode()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = ser.load_pem_public_key(public_key_pem)
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
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])

    assert response.status_code == 201
    assert response.json()["status"] == "delivery_failed"


def test_submit_result_only_allowed_by_target_installation(client):
    sender, sender_key = register_installation(client)
    target, target_key = register_installation(client)

    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

    handover_payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    handover = _signed_post(
        client, "/handovers", handover_payload, sender_key, installation_id=sender["id"]
    ).json()

    result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
    wrong_caller = _signed_post(
        client,
        f"/handovers/{handover['id']}/result",
        result_payload,
        sender_key,
        installation_id=sender["id"],
    )
    assert wrong_caller.status_code == 403

    correct_caller = _signed_post(
        client,
        f"/handovers/{handover['id']}/result",
        result_payload,
        target_key,
        installation_id=target["id"],
    )
    assert correct_caller.status_code == 200
    assert correct_caller.json()["status"] == "completed"


def test_get_handover_unknown_returns_404(client):
    assert client.get("/handovers/does-not-exist").status_code == 404


def test_sign_body_signature_is_verifiable_helper_smoke():
    """Reiner Sanity-Check der Krypto-Hilfsfunktion selbst, unabhängig vom
    HTTP-Rundlauf oben."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    key = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=ser.Encoding.PEM,
        format=ser.PrivateFormat.PKCS8,
        encryption_algorithm=ser.NoEncryption(),
    )
    body = b'{"hello":"world"}'
    signature_b64 = sign_body(private_pem, body)

    key.public_key().verify(
        base64.b64decode(signature_b64),
        body,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
