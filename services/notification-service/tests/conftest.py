import os
import uuid

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from notification_service.models import Base
from notification_service.settings import Settings
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing".
os.environ["DMS_POSTGRES_DSN"] = DSN
NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
os.environ["DMS_NATS_URL"] = NATS_URL
SMTP_HOST = os.environ.get("TEST_SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("TEST_SMTP_PORT", "1025"))
os.environ["DMS_SMTP_HOST"] = SMTP_HOST
os.environ["DMS_SMTP_PORT"] = str(SMTP_PORT)


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS notification"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE notification.notification CASCADE"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS notification"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
def settings() -> Settings:
    return Settings()


AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")


@pytest.fixture
async def real_recipient():
    """Echtes `auth-service`-Konto (kein Mocking, P6-S6-Retrofit: `POST
    /notifications` prüft die Empfänger-Existenz) - erzeugt über das
    bestehende technische `users-admin`-Konto (P6-S5), am Ende wieder
    gelöscht. Liefert `(username, email)`."""
    username = f"notif-test-{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"
    async with httpx.AsyncClient(base_url=AUTH_SERVICE_URL) as client:
        token = (
            await client.post("/login", json={"username": "users-admin", "password": "users-admin"})
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = (
            await client.post(
                "/users",
                json={
                    "username": username,
                    "email": email,
                    "password": "testpass123",
                    "first_name": "Notif",
                    "last_name": "Test",
                },
                headers=headers,
            )
        ).json()
        yield username, email
        await client.delete(f"/users/{created['id']}", headers=headers)
