import json
import os
import uuid

import pytest
from auth_service import federation_crypto
from auth_service.bootstrap import DOMAIN_ADMIN_USERS_USERNAME
from auth_service.main import _FEDERATION_IDENTITY_ID
from auth_service.models import FederationIdentity
from dms_db_base import build_engine, make_session_factory
from fastapi.testclient import TestClient

DSN = os.environ.get(
    "TEST_POSTGRES_DSN", "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"
)


def _user_payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"directory-test-{suffix}",
        "email": f"directory-test-{suffix}@example.com",
        "password": "testpass123",
        "first_name": "Verzeichnis",
        "last_name": f"Test{suffix}",
    }
    payload.update(overrides)
    return payload


def test_search_directory_requires_authentication(client):
    response = client.get("/users/directory", params={"q": "does-not-matter"})
    assert response.status_code == 401


def test_search_directory_finds_user_by_substring(client, domain_admin_auth_headers):
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()

    response = client.get(
        "/users/directory", params={"q": payload["last_name"]}, headers=domain_admin_auth_headers
    )

    assert response.status_code == 200
    ids = [entry["id"] for entry in response.json()]
    assert created["id"] in ids
    match = next(e for e in response.json() if e["id"] == created["id"])
    assert match["email"] == payload["email"]
    # Kein `enabled`-Feld im Verzeichnis-Eintrag, anders als GET /users.
    assert "enabled" not in match

    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_search_directory_available_to_regular_users_not_just_admins(client, test_user):
    """Lokal, immer verfügbar (2.5) - anders als `GET /users` bewusst OHNE
    `admin.user_management`-Gate."""
    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    response = client.get("/users/directory", params={"q": test_user["username"]}, headers=headers)

    assert response.status_code == 200


def test_directory_federation_status_disabled_by_default(client):
    response = client.get("/users/directory/federation-status")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "peer_installation_count": 0}


def test_search_federated_directory_returns_403_when_disabled(client, domain_admin_auth_headers):
    response = client.get(
        "/users/directory/federated", params={"q": "x"}, headers=domain_admin_auth_headers
    )
    assert response.status_code == 403


def test_federated_search_inbound_returns_403_when_disabled(client):
    response = client.post(
        "/users/directory/federated-search-inbound",
        content=json.dumps({"query": "x"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403


@pytest.fixture
def federation_enabled(monkeypatch):
    """Echte Selbst-Registrierung am laufenden Federation Hub (7.4) - gleiches
    Selbst-Loopback-Testmuster wie überall sonst in diesem Projekt
    (federation-hub-service P6-S9, mail-connector P15-S3)."""
    import auth_service.main as main_module

    hub_url = "http://localhost:8018"
    monkeypatch.setattr(main_module.settings, "federation_hub_base_url", hub_url)
    monkeypatch.setattr(main_module.settings, "federated_directory_enabled", True)
    with TestClient(main_module.app) as c:
        yield c


async def _fetch_identity() -> FederationIdentity:
    engine = build_engine(DSN)
    factory = make_session_factory(engine)
    async with factory() as session:
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    await engine.dispose()
    assert identity is not None
    return identity


async def test_federation_registration_creates_identity(federation_enabled):
    identity = await _fetch_identity()
    assert identity.installation_id
    assert identity.private_key_pem
    assert identity.public_key_pem


async def test_federated_search_inbound_accepts_valid_self_signed_request(federation_enabled):
    """Testet den echten Signaturprüfungs-Pfad (Live-Abruf des öffentlichen
    Schlüssels vom Hub) - eine Installation, die sich selbst anfragt, ist
    kein realer Anwendungsfall (siehe `directory_federation.eligible_peers`,
    das die eigene `installation_id` ausschließt), beweist aber echt, dass
    Signieren/Verifizieren gegen den echten, laufenden Hub funktioniert."""
    identity = await _fetch_identity()
    body = json.dumps({"query": "directory-test"}).encode("utf-8")
    signature = federation_crypto.sign_body(identity.private_key_pem, body)

    response = federation_enabled.post(
        "/users/directory/federated-search-inbound",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Installation-Id": identity.installation_id,
            "X-Installation-Signature": signature,
        },
    )

    assert response.status_code == 200


async def test_federated_search_inbound_rejects_invalid_signature(federation_enabled):
    identity = await _fetch_identity()
    body = json.dumps({"query": "x"}).encode("utf-8")

    response = federation_enabled.post(
        "/users/directory/federated-search-inbound",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Installation-Id": identity.installation_id,
            "X-Installation-Signature": "bm90LWEtcmVhbC1zaWduYXR1cmU=",
        },
    )

    assert response.status_code == 401


async def test_federated_search_inbound_rejects_unknown_installation(federation_enabled):
    body = json.dumps({"query": "x"}).encode("utf-8")

    response = federation_enabled.post(
        "/users/directory/federated-search-inbound",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Installation-Id": "does-not-exist",
            "X-Installation-Signature": "bm90LWEtcmVhbC1zaWduYXR1cmU=",
        },
    )

    assert response.status_code == 401


async def test_search_federated_directory_excludes_self(federation_enabled):
    """Die eigene, gerade erst registrierte Installation taucht nie als Peer
    in den eigenen Suchergebnissen auf (`eligible_peers` schließt die eigene
    `installation_id` explizit aus) - ohne einen zweiten, echten Peer bleibt
    das Ergebnis leer, aber der Aufruf selbst (Hub-Adressbuch abrufen, eigene
    Identität laden) muss fehlerfrei durchlaufen. Login über denselben
    `federation_enabled`-Client statt der separaten `client`/
    `domain_admin_auth_headers`-Fixtures - ein zweiter, unabhängiger
    `TestClient(app)` auf demselben Prozess würde einen zweiten
    NATS-Durable-Konsumenten auf demselben Namen registrieren und mit einem
    "already bound"-Fehler kollidieren."""
    login = federation_enabled.post(
        "/login",
        json={"username": DOMAIN_ADMIN_USERS_USERNAME, "password": DOMAIN_ADMIN_USERS_USERNAME},
    )
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    response = federation_enabled.get(
        "/users/directory/federated", params={"q": "x"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == []
