from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from license_factory import make_license_token
from license_service.main import app


@pytest.fixture
def client():
    """Externe Clients durch AsyncMock ersetzt - identisches Muster wie
    query-service's `test_api.py`-Fixture. `app.state.event_bus` bleibt der
    echte, in der Lifespan verbundene `NatsEventBusClient` (kein Mock)."""
    with TestClient(app) as c:
        app.state.storage_client = AsyncMock()
        app.state.storage_client.total_bytes.return_value = 0
        app.state.document_client = AsyncMock()
        app.state.document_client.count_active_total.return_value = 0
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.auth_client.concurrent_session_count.return_value = 0
        app.state.auth_client.named_user_count.return_value = 0
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "license-service"


def test_license_status_without_installed_license(client):
    response = client.get("/license/status")
    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is False
    assert body["valid"] is False


def test_upload_requires_principal_header(client):
    token = make_license_token()
    response = client.post("/license", json={"license_token": token})
    assert response.status_code == 403


def test_upload_requires_license_permission(client):
    app.state.permission_client.has_permission.return_value = False
    token = make_license_token()
    response = client.post(
        "/license", json={"license_token": token}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 403


def test_upload_with_invalid_signature_rejected(client):
    response = client.post(
        "/license",
        json={"license_token": "not-a-valid-jwt"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 400


def test_upload_valid_license_succeeds_and_status_reflects_it(client):
    token = make_license_token(max_users=10, storage_limit_gb=50.0, document_limit=500)

    response = client.post(
        "/license", json={"license_token": token}, headers={"x-dms-principal": "alice"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["installed"] is True
    assert body["valid"] is True
    assert body["users"]["limit"] == 10
    assert body["storage_gb"]["limit"] == 50.0
    assert body["documents"]["limit"] == 500

    status_response = client.get("/license/status")
    assert status_response.status_code == 200
    assert status_response.json()["installed"] is True


def test_upload_expired_license_is_accepted_but_reported_invalid(client):
    token = make_license_token(expires_in_days=-5, issued_days_ago=400)

    response = client.post(
        "/license", json={"license_token": token}, headers={"x-dms-principal": "alice"}
    )

    assert response.status_code == 201
    assert response.json()["valid"] is False
    assert response.json()["invalid_reason"] == "Lizenz abgelaufen"


def test_upload_allowed_for_active_superuser_without_permission(client):
    app.state.permission_client.has_permission.return_value = False
    app.state.auth_client.get_active_superuser.return_value = (True, "root-user")
    token = make_license_token()

    response = client.post(
        "/license", json={"license_token": token}, headers={"x-dms-principal": "root-user"}
    )

    assert response.status_code == 201


def test_status_shows_exceeded_dimension(client):
    app.state.auth_client.concurrent_session_count.return_value = 99
    token = make_license_token(max_users=5)
    client.post("/license", json={"license_token": token}, headers={"x-dms-principal": "alice"})

    response = client.get("/license/status")

    body = response.json()
    assert body["users"]["exceeded"] is True
    assert "users" in body["limits_exceeded"]
