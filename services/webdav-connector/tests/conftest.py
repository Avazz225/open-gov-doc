import os
import uuid

import httpx
import pytest

AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")

_TEST_PASSWORD = "testpass123"


@pytest.fixture
def real_user():
    """Echtes `auth-service`-Konto (kein Mocking, gleiches Muster wie
    `signature-service`s `real_signer`-Fixture) - WebDAV-Basic-Auth (siehe
    `DmsAuthDomainController`) braucht echte, gegen `auth-service` prüfbare
    Zugangsdaten, kein Bearer-Token. Liefert `(username, password)`, Anlage/
    Löschung über das technische `users-admin`-Konto (P6-S5)."""
    username = f"webdav-test-{uuid.uuid4().hex[:8]}"
    with httpx.Client(base_url=AUTH_SERVICE_URL, timeout=10.0) as client:
        token = client.post(
            "/login", json={"username": "users-admin", "password": "users-admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post(
            "/users",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": _TEST_PASSWORD,
                "first_name": "WebDAV",
                "last_name": "Test",
            },
            headers=headers,
        ).json()
        yield username, _TEST_PASSWORD
        client.delete(f"/users/{created['id']}", headers=headers)
