import os
import uuid

import httpx
import pytest

AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")

_TEST_PASSWORD = "testpass123"


def _create_real_user(client: httpx.Client, headers: dict) -> dict:
    username = f"cmis-test-{uuid.uuid4().hex[:8]}"
    return client.post(
        "/users",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": _TEST_PASSWORD,
            "first_name": "CMIS",
            "last_name": "Test",
        },
        headers=headers,
    ).json()


@pytest.fixture
def real_user():
    """Echtes `auth-service`-Konto (kein Mocking, gleiches Muster wie
    `webdav-connector`s `real_user`-Fixture, P12-S1) - CMIS-Basic-Auth
    (siehe `cmis_connector.auth`) braucht echte, gegen `auth-service`
    prüfbare Zugangsdaten. Liefert `(username, password)`."""
    with httpx.Client(base_url=AUTH_SERVICE_URL, timeout=10.0) as client:
        token = client.post(
            "/login", json={"username": "users-admin", "password": "users-admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = _create_real_user(client, headers)
        yield created["username"], _TEST_PASSWORD
        client.delete(f"/users/{created['id']}", headers=headers)


@pytest.fixture
def second_real_user():
    """Zweites, unabhängiges Konto - gebraucht, um einen echten
    Checkout-Konflikt zu simulieren (`document-service`s Sperrprüfung
    vergleicht `locked_by`, nicht `session_id` - derselbe Akteur darf seine
    eigene Sperre jederzeit erneut "erwerben", siehe
    docs/services/cmis-connector.md)."""
    with httpx.Client(base_url=AUTH_SERVICE_URL, timeout=10.0) as client:
        token = client.post(
            "/login", json={"username": "users-admin", "password": "users-admin"}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = _create_real_user(client, headers)
        yield created["username"], _TEST_PASSWORD
        client.delete(f"/users/{created['id']}", headers=headers)
