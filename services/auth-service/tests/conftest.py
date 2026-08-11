import os
import uuid

import pytest
from dms_db_base import build_engine
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings wie für die Test-Fixtures unten -
# sonst testet TestClient(app) unbemerkt an TEST_POSTGRES_DSN vorbei die
# Live-DB, siehe PROGRESS.md "Tooling & Testing". Erstes eigenes Postgres-
# Schema dieses Service überhaupt (P15-S4, Federation-Identität).
os.environ["DMS_POSTGRES_DSN"] = DSN

from auth_service.bootstrap import (  # noqa: E402
    DOMAIN_ADMIN_USERS_USERNAME,
    ensure_realm_and_client,
)
from auth_service.main import app  # noqa: E402
from auth_service.models import Base  # noqa: E402
from auth_service.settings import Settings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from keycloak import KeycloakAdmin  # noqa: E402

settings = Settings()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap():
    ensure_realm_and_client(settings)


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE auth.federation_identity"))
    await eng.dispose()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def domain_admin_auth_headers(client) -> dict[str, str]:
    """Login als das technische `users-admin`-Konto (4.6, P6-S5) - für alle
    Tests, die `/users` (jetzt hinter der Domäne "Nutzer-/Rechteverwaltung"
    gegated) aufrufen. Setzt voraus, dass `permission-service` erreichbar ist
    (echte Rollenzuweisung beim App-Start, kein Mocking)."""
    response = client.post(
        "/login",
        json={"username": DOMAIN_ADMIN_USERS_USERNAME, "password": DOMAIN_ADMIN_USERS_USERNAME},
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def keycloak_admin():
    admin = KeycloakAdmin(
        server_url=settings.keycloak_base_url,
        username=settings.keycloak_admin_username,
        password=settings.keycloak_admin_password,
        realm_name="master",
        user_realm_name="master",
    )
    admin.change_current_realm(settings.keycloak_realm)
    return admin


@pytest.fixture
def test_user(keycloak_admin):
    """Ein frischer Realm-Nutzer je Test. `firstName`/`lastName` sind nötig,
    sonst löst Keycloaks Default-User-Profile beim Login `VERIFY_PROFILE`
    aus ("Account is not fully set up") statt Tokens auszustellen.
    """
    username = f"test-{uuid.uuid4().hex[:8]}"
    password = "testpass123"
    user_id = keycloak_admin.create_user(
        payload={
            "username": username,
            "enabled": True,
            "email": f"{username}@example.com",
            "emailVerified": True,
            "firstName": "Test",
            "lastName": "User",
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
        exist_ok=True,
    )
    yield {"username": username, "password": password}
    keycloak_admin.delete_user(user_id)
