import os
import uuid

import httpx
import pytest

AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")

_TEST_PASSWORD = "testpass123"
# `POST /roles` requires `admin.user_management` since ADR 0071 (and `PUT
# /approval-config/{action_type}` since P32-S1/ADR 0130) - `test_webdav.py`'s
# `_grant_document_write` needs an authorized test principal for both, same
# pattern as `document-service`'s/`folder-service`'s
# `ROLE_ADMIN_PRINCIPAL_ID`/`_grant_role_admin_permission`.
ROLE_ADMIN_PRINCIPAL_ID = "webdav-connector-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
def _grant_role_admin_permission():
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = pc.get(
            "/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID}
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ROLE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


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
