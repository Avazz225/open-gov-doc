import base64
import json
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dms_db_base import build_engine, make_session_factory
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from federation_hub_service import repository
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


def test_ca_certificate_endpoint_returns_a_valid_self_signed_certificate(client):
    """Post-Roadmap Phase 21 Session 2 (ADR 0085) - Certificate-Pinning-
    Äquivalent zu `GET /public-key`: Installationen können dieses Zertifikat
    beim ersten Kontakt abrufen und pinnen."""
    from cryptography import x509

    response = client.get("/ca-certificate")
    assert response.status_code == 200
    ca_certificate_pem = response.json()["ca_certificate_pem"]
    assert "BEGIN CERTIFICATE" in ca_certificate_pem
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem.encode("utf-8"))
    assert ca_cert.issuer == ca_cert.subject
    ca_cert.verify_directly_issued_by(ca_cert)

    second_response = client.get("/ca-certificate")
    assert second_response.json()["ca_certificate_pem"] == ca_certificate_pem


def test_register_installation_response_includes_a_valid_certificate(client):
    from cryptography import x509

    installation, _ = register_installation(client)
    assert installation["certificate_pem"] is not None
    assert installation["certificate_not_after"] is not None

    ca_certificate_pem = client.get("/ca-certificate").json()["ca_certificate_pem"]
    ca_cert = x509.load_pem_x509_certificate(ca_certificate_pem.encode("utf-8"))
    cert = x509.load_pem_x509_certificate(installation["certificate_pem"].encode("utf-8"))
    cert.verify_directly_issued_by(ca_cert)


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


def test_list_installations_respects_limit(client):
    """P62-S1: previously fully unbounded."""
    register_installation(client)
    register_installation(client)

    response = client.get("/installations", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()) == 1


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


def test_rotate_key_reissues_a_certificate_bound_to_the_new_key(client):
    """Post-Roadmap Phase 21 Session 2 (ADR 0085) - ein Zertifikat für den
    ALTEN Schlüssel wäre nach der Rotation nicht mehr zutreffend."""
    installation, private_pem = register_installation(client)
    original_certificate_pem = installation["certificate_pem"]
    _, new_public_pem = _generate_keypair()

    response = _signed_post(
        client,
        f"/installations/{installation['id']}/rotate-key",
        {"new_public_key_pem": new_public_pem},
        private_pem,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["certificate_pem"] is not None
    assert body["certificate_pem"] != original_certificate_pem


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


def test_create_handover_rejects_undeclared_process_type(client):
    """P63-S2: `supported_process_types` was previously stored at
    registration but never checked - a handover of an undeclared type
    succeeded at the hub and only failed downstream once the target
    installation itself rejected it."""
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, supported_process_types=["allowed-process"])

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "not-the-declared-process",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])
    assert response.status_code == 422


def test_create_handover_allows_declared_process_type(client):
    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, supported_process_types=["allowed-process"])

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "allowed-process",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])
    assert response.status_code == 201
    assert response.json()["status"] == "delivered"


def test_create_handover_allows_any_process_type_when_none_declared(client):
    """An empty `supported_process_types` list (the default - nothing sets
    this field at registration in practice today) means "no restriction
    declared", not "accepts nothing" - otherwise this check would reject
    every handover that exists in the real system today."""
    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client)

    payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "anything-at-all",
        "encrypted_payload": "opaque",
    }
    response = _signed_post(client, "/handovers", payload, sender_key, installation_id=sender["id"])
    assert response.status_code == 201
    assert response.json()["status"] == "delivered"


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


_RETRY_OPERATOR_KEY = "operator-secret-for-test"


def _retry(client, handover_id: str, *, key: str | None = _RETRY_OPERATOR_KEY):
    """Phase 44 Session 1/ADR 0162: `/retry` is gated the same way as
    `/revoke` - every retry call in this file needs the operator-key
    header now, factored here so the ~six call sites don't repeat it."""
    headers = {"Authorization": f"Bearer {key}"} if key is not None else {}
    return client.post(f"/handovers/{handover_id}/retry", headers=headers)


async def _has_pending_payload(session_factory, handover_id: str, *, leg: str) -> bool:
    """Post-Roadmap Phase 73 Session 3 (ADR 0213): replaces the old
    `handover_id in app.state.pending_handover_payloads`/`..._result_
    payloads` membership checks. Deliberately queries through the test's
    OWN `session_factory` fixture (a separate engine/connection from
    `app.state.session_factory`, which `client`'s `TestClient(app)` lifespan
    created on its own event loop) rather than reaching into `app.state` -
    this is exactly the "no in-process state shared with the writer"
    shape the ADR's fix is about, not merely a style preference."""
    async with session_factory() as session:
        return await repository.get_pending_payload(session, handover_id, leg=leg) is not None


async def _drop_pending_payload(session_factory, handover_id: str, *, leg: str) -> None:
    """Simulates the defensive-fallback edge case (a genuinely missing/
    corrupted row) that `_retry_forward_delivery`/`_retry_result_delivery`
    still guard against with a `409` even after ADR 0213 - NOT a restart
    simulation (see `test_main.py` for that; a real restart no longer loses
    this row at all, which is the whole point of ADR 0213)."""
    async with session_factory() as session:
        await repository.delete_pending_payload(session, handover_id, leg=leg)
        await session.commit()


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


async def test_create_handover_marks_pending_retry_on_unreachable_target(client, session_factory):
    """Post-Roadmap Phase 20 Session 5 (ADR 0081): ein transienter erster
    Fehlschlag landet nicht mehr sofort im terminalen `delivery_failed`,
    sondern im retry-fähigen `pending_retry` - solange
    `max_handover_delivery_attempts` (Default 5) noch nicht erschöpft ist."""
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
    body = response.json()
    assert body["status"] == "pending_retry"
    assert body["attempts"] == 1
    assert body["next_retry_at"] is not None
    # Der Payload wird für den späteren Retry (Poll-Loop oder manueller
    # `POST .../retry`) persistent gehalten (ADR 0213) - über eine eigene,
    # von der App losgelöste Session geprüft.
    assert await _has_pending_payload(session_factory, body["id"], leg="forward")


async def test_create_handover_reaches_delivery_failed_after_exhausting_attempts(
    client, session_factory
):
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        response = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "delivery_failed"
        assert body["attempts"] == 1
        assert body["next_retry_at"] is None
        # Bleibt trotz Erschöpfung im Cache - genau dafür ist er da: ein
        # manueller `POST .../retry` braucht ihn jetzt. Nur ein ERFOLGREICHER
        # Versuch entfernt den Eintrag wieder (ADR 0081), persistent seit
        # ADR 0213.
        assert await _has_pending_payload(session_factory, body["id"], leg="forward")
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts


def test_retry_handover_requires_delivery_failed_status(client):
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
    created = _signed_post(
        client, "/handovers", payload, sender_key, installation_id=sender["id"]
    ).json()
    assert created["status"] == "pending_retry"

    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        response = _retry(client, created["id"])
        assert response.status_code == 409
    finally:
        hub_settings.hub_operator_key = None


async def test_retry_handover_reattempts_a_delivery_failed_handover(client, session_factory):
    """ADR 0081: manueller Neustart setzt `attempts`/`next_retry_at` VOR dem
    erneuten Versuch zurück (`repository.reset_for_retry`) - andernfalls
    zählt `mark_handover_delivered` von der bereits erschöpften Zahl weiter
    (gleicher Bug wie in ADR 0080 für ocr-/rendering-service dokumentiert)."""
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
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
        assert created["status"] == "delivery_failed"
        # Bleibt nach Erschöpfung im Cache (ADR 0081) - kein manuelles
        # Nachhelfen nötig, das ist der eigentliche Zweck des Fixes.
        assert await _has_pending_payload(session_factory, created["id"], leg="forward")

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        response = _retry(client, created["id"])
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "delivered"
        # attempts zaehlt nur Fehlschlaege - reset_for_retry setzt sie auf 0
        # zurueck, ein erfolgreicher Zustellversuch erhoeht sie nicht.
        assert body["attempts"] == 0
        assert len(received) == 1
        assert not await _has_pending_payload(session_factory, created["id"], leg="forward")
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts
        hub_settings.hub_operator_key = None


async def test_retry_handover_without_cached_payload_returns_409(client, session_factory):
    """Deckt die verbleibende Grenze ab (ADR 0213 "Rationale": defensiver
    Fallback für eine tatsächlich fehlende/beschädigte Zeile, in der Praxis
    sollte das nicht vorkommen - ANDERS als vor ADR 0213 ist ein normaler
    Hub-Neustart hierfür NICHT mehr die Ursache, siehe `test_main.py` für
    den eigentlichen Restart-Beweis): fehlt die zwischengespeicherte Payload
    dennoch, kann ein manueller Retry nicht automatisch nachgestellt
    werden."""
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
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
        assert created["status"] == "delivery_failed"
        assert await _has_pending_payload(session_factory, created["id"], leg="forward")

        # Simuliert eine fehlende/beschädigte Zeile (nicht mehr durch einen
        # Neustart erreichbar seit ADR 0213, siehe Docstring oben).
        await _drop_pending_payload(session_factory, created["id"], leg="forward")

        response = _retry(client, created["id"])
        assert response.status_code == 409
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts
        hub_settings.hub_operator_key = None


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


async def test_submit_result_marks_result_pending_retry_on_unreachable_origin(
    client, session_factory
):
    """Return-path retry (Phase 40 Session 3, mirrors
    `test_create_handover_marks_pending_retry_on_unreachable_target` for the
    OTHER leg): a transient failure delivering the result back to the
    origin installation lands in the retry-capable `result_pending_retry`,
    not immediately in the terminal `result_delivery_failed`."""
    sender, sender_key = register_installation(
        client, callback_base_url="http://unreachable.invalid"
    )
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
    assert handover["status"] == "delivered"

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
    result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
    response = _signed_post(
        client,
        f"/handovers/{handover['id']}/result",
        result_payload,
        target_key,
        installation_id=target["id"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "result_pending_retry"
    assert body["result_attempts"] == 1
    assert body["result_next_retry_at"] is not None
    assert await _has_pending_payload(session_factory, body["id"], leg="result")


async def test_submit_result_reaches_result_delivery_failed_after_exhausting_attempts(
    client, session_factory
):
    sender, sender_key = register_installation(
        client, callback_base_url="http://unreachable.invalid"
    )
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

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    try:
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        response = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "result_delivery_failed"
        assert body["result_attempts"] == 1
        assert body["result_next_retry_at"] is None
        assert await _has_pending_payload(session_factory, body["id"], leg="result")
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts


async def test_retry_handover_reattempts_a_result_delivery_failed_handover(client, session_factory):
    """Return-path counterpart of
    `test_retry_handover_reattempts_a_delivery_failed_handover` - the SAME
    `POST .../retry` endpoint dispatches on the current status, no separate
    endpoint needed for the admin UI to call."""
    sender, sender_key = register_installation(
        client, callback_base_url="http://unreachable.invalid"
    )
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

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        created = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert created["status"] == "result_delivery_failed"

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        response = _retry(client, handover["id"])
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "completed"
        assert body["result_attempts"] == 0
        assert len(received) == 1
        assert not await _has_pending_payload(session_factory, handover["id"], leg="result")
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts
        hub_settings.hub_operator_key = None


async def test_retry_handover_without_cached_result_payload_returns_409(client, session_factory):
    sender, sender_key = register_installation(
        client, callback_base_url="http://unreachable.invalid"
    )
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

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        result_payload = {"outcome": "completed", "encrypted_result": "opaque-result"}
        created = _signed_post(
            client,
            f"/handovers/{handover['id']}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        ).json()
        assert created["status"] == "result_delivery_failed"

        await _drop_pending_payload(session_factory, handover["id"], leg="result")

        response = _retry(client, handover["id"])
        assert response.status_code == 409
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts
        hub_settings.hub_operator_key = None


def test_retry_handover_rejects_a_status_other_than_the_two_failure_states(client):
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client)

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

    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        response = _retry(client, created["id"])
        assert response.status_code == 409
    finally:
        hub_settings.hub_operator_key = None


def test_retry_handover_requires_hub_operator_key(client):
    """Phase 44 Session 1/ADR 0162: the endpoint used to have no auth model
    at all - this proves the new gate actually rejects a call carrying no
    `Authorization` header, mirroring `test_revoke_requires_hub_operator_key`
    above. No `hub_operator_key` configured -> fully locked (403), same
    fail-closed default as revoke."""
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
    created = _signed_post(
        client, "/handovers", payload, sender_key, installation_id=sender["id"]
    ).json()
    assert created["status"] == "pending_retry"

    response = _retry(client, created["id"], key=None)
    assert response.status_code == 403


def test_retry_handover_with_wrong_operator_key_returns_403(client):
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
    created = _signed_post(
        client, "/handovers", payload, sender_key, installation_id=sender["id"]
    ).json()
    assert created["status"] == "pending_retry"

    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        response = _retry(client, created["id"], key="wrong-secret")
        assert response.status_code == 403
    finally:
        hub_settings.hub_operator_key = None


def test_list_handovers_filters_by_status(client):
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))
    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    try:
        failed_payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        failed = _signed_post(
            client, "/handovers", failed_payload, sender_key, installation_id=sender["id"]
        ).json()
        assert failed["status"] == "delivery_failed"
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts

    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))
    ok_target, _ = register_installation(client)
    ok_payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": ok_target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    delivered = _signed_post(
        client, "/handovers", ok_payload, sender_key, installation_id=sender["id"]
    ).json()
    assert delivered["status"] == "delivered"

    unfiltered = client.get("/handovers")
    assert unfiltered.status_code == 200
    ids = {h["id"] for h in unfiltered.json()}
    assert {failed["id"], delivered["id"]} <= ids

    filtered = client.get("/handovers", params={"status": "delivery_failed"})
    assert filtered.status_code == 200
    filtered_ids = {h["id"] for h in filtered.json()}
    assert failed["id"] in filtered_ids
    assert delivered["id"] not in filtered_ids


def test_list_handovers_respects_limit(client):
    """P62-S1: previously fully unbounded."""
    sender, sender_key = register_installation(client)
    stub, _ = _make_stub_receiver()
    app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))
    target, _ = register_installation(client)

    for _ in range(2):
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        response = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        )
        assert response.json()["status"] == "delivered"

    response = client.get("/handovers", params={"limit": 1})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_get_handover_unknown_returns_404(client):
    assert client.get("/handovers/does-not-exist").status_code == 404


def test_metrics_endpoint_exposes_retry_cache_sensors(client):
    """Phase 40 Session 4 - this service's first sensors at all."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "federation_hub_retry_cache_forward_pending" in response.text
    assert "federation_hub_retry_cache_result_pending" in response.text


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


def test_create_handover_rejects_oversized_payload(client):
    """DMS-to-DMS XDOMEA handoff (7.4/14.2, Post-Roadmap Phase 43 Session 1,
    ADR 0147/ADR 0159): a bounded, explicit ceiling on `encrypted_payload`,
    checked before any target/version lookup - the real memory-pressure
    risk this mitigates (`pending_handover_payloads`, ADR 0081) applies
    regardless of whether the handover would otherwise have succeeded."""
    sender, sender_key = register_installation(client)
    hub_settings.max_handover_payload_chars = 100
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": "does-not-matter-checked-first",
            "process_type": "test-process",
            "encrypted_payload": "x" * 101,
        }
        response = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        )
        assert response.status_code == 413
    finally:
        hub_settings.max_handover_payload_chars = 100_000_000


def test_create_handover_allows_payload_at_the_limit(client):
    sender, sender_key = register_installation(client)
    target, _ = register_installation(client)
    hub_settings.max_handover_payload_chars = 100
    try:
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "x" * 100,
        }
        response = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        )
        assert response.status_code == 201
    finally:
        hub_settings.max_handover_payload_chars = 100_000_000


def test_submit_handover_result_rejects_oversized_payload(client):
    sender, sender_key = register_installation(client)
    target, target_key = register_installation(client)
    handover_payload = {
        "handover_id": str(uuid.uuid4()),
        "to_installation_id": target["id"],
        "process_type": "test-process",
        "encrypted_payload": "opaque",
    }
    create_response = _signed_post(
        client, "/handovers", handover_payload, sender_key, installation_id=sender["id"]
    )
    assert create_response.status_code == 201
    handover_id = create_response.json()["id"]

    hub_settings.max_handover_payload_chars = 100
    try:
        result_payload = {"outcome": "completed", "encrypted_result": "x" * 101}
        response = _signed_post(
            client,
            f"/handovers/{handover_id}/result",
            result_payload,
            target_key,
            installation_id=target["id"],
        )
        assert response.status_code == 413
    finally:
        hub_settings.max_handover_payload_chars = 100_000_000


# --- Restart-survival proof (Post-Roadmap Phase 73 Session 3, ADR 0213) ---


async def test_manual_retry_survives_a_simulated_hub_restart(client):
    """The literal claim ADR 0213 makes, proven end to end rather than just
    asserted against the repository functions in isolation: `create_handover`
    persists the payload via `app.state.session_factory` (the session the
    running app itself used); this test then reads it back via a
    COMPLETELY SEPARATE engine/connection pool it builds itself right here
    (its own `build_engine(...)`/`make_session_factory(...)` call, sharing
    no Python object whatsoever with `app.state.engine`) before ever
    touching `POST /handovers/{id}/retry`. That separate-engine read
    succeeding is exactly what "survives a hub restart" means for a
    payload that, before ADR 0213, existed only as a Python dict entry tied
    to the ONE process/session that wrote it - a restart would have
    discarded it before any engine, fresh or not, could ever see it again.
    `POST .../retry` is then driven through the (unavoidably single, still
    live) app instance to prove the persisted row is also genuinely usable
    for a real redelivery, not merely present as an inert row."""
    fresh_engine = build_engine(hub_settings.postgres_dsn)
    fresh_session_factory = make_session_factory(fresh_engine)

    def _raise(*_args, **_kwargs):
        raise httpx.ConnectError("no route", request=httpx.Request("POST", "http://x"))

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_raise))

    original_max_attempts = hub_settings.max_handover_delivery_attempts
    hub_settings.max_handover_delivery_attempts = 1
    hub_settings.hub_operator_key = _RETRY_OPERATOR_KEY
    try:
        sender, sender_key = register_installation(client)
        target, _ = register_installation(client, callback_base_url="http://unreachable.invalid")
        payload = {
            "handover_id": str(uuid.uuid4()),
            "to_installation_id": target["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }
        created = _signed_post(
            client, "/handovers", payload, sender_key, installation_id=sender["id"]
        ).json()
        assert created["status"] == "delivery_failed"

        # Reads through the fresh, unrelated engine built above - not
        # `app.state.session_factory`, not even the `session_factory`
        # pytest fixture (itself already a separate engine, but this one is
        # built fresh right in this test body for maximum clarity).
        async with fresh_session_factory() as fresh_session:
            cached = await repository.get_pending_payload(
                fresh_session, created["id"], leg="forward"
            )
        assert cached == {
            "handover_id": created["id"],
            "from_installation_id": sender["id"],
            "process_type": "test-process",
            "encrypted_payload": "opaque",
        }

        stub, received = _make_stub_receiver()
        app.state.http_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=stub))

        response = _retry(client, created["id"])
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "delivered"
        assert len(received) == 1
    finally:
        hub_settings.max_handover_delivery_attempts = original_max_attempts
        hub_settings.hub_operator_key = None
        await fresh_engine.dispose()
