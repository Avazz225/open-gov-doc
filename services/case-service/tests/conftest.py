import os

import httpx
import pytest
from case_service.models import Base
from dms_db_base import build_engine, make_session_factory
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

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
os.environ["DMS_WORKFLOW_SERVICE_BASE_URL"] = WORKFLOW_SERVICE_URL
os.environ["DMS_DOCUMENT_SERVICE_BASE_URL"] = DOCUMENT_SERVICE_URL
os.environ["DMS_OBJECT_TYPE_SERVICE_BASE_URL"] = OBJECT_TYPE_SERVICE_URL
os.environ["DMS_PERMISSION_SERVICE_BASE_URL"] = PERMISSION_SERVICE_URL

# Fester Principal statt uuid4() je Testlauf, damit die Rollenzuweisung
# idempotent bleibt (Unique-Constraint auf permission-service-Seite) - echter
# Aufruf gegen den laufenden permission-service (kein Mocking). Seit P6-S6
# verlangt workflow-service's `POST /process-definitions` die Capability
# `admin.object_config` (siehe workflow-service/tests/conftest.py, gleiches
# Muster hier dupliziert statt geteilt, da beide Services unabhängig
# testbar bleiben sollen).
CONFIG_ADMIN_PRINCIPAL_ID = "case-service-test-config-admin"


@pytest.fixture
def workflow_admin_headers() -> dict[str, str]:
    return {"X-DMS-Principal": CONFIG_ADMIN_PRINCIPAL_ID}


# RBAC (Post-Roadmap Phase 19 Session 5, ADR 0070) - case-service prüft seit
# dieser Session `case.read`/`case.write` gegen permission-service. Die
# "everyone"-Gruppe (ADR 0067) gewährt beides standardmäßig jedem
# authentifizierten Principal - kein spezielles Rollen-Setup für den
# Positivfall nötig, nur ein nicht-leerer `X-DMS-Principal`.
CASE_TEST_PRINCIPAL_ID = "case-service-tests"


@pytest.fixture
def case_headers() -> dict[str, str]:
    return {"X-DMS-Principal": CASE_TEST_PRINCIPAL_ID}


@pytest.fixture
def everyone_role_without():
    """Entfernt eine Berechtigung temporär aus der geseedeten "everyone"-
    Rolle, um den Negativpfad (fehlende Berechtigung -> 403) zu beweisen -
    stellt die ursprüngliche Liste danach wieder her. Gleiches Muster wie
    `auth-service/tests/conftest.py::everyone_role_without`, hier dupliziert
    statt geteilt (beide Services unabhängig testbar, Projektkonvention)."""
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        everyone = next(r for r in roles if r["name"] == "everyone")
        original_permissions = list(everyone["permissions"])

        def _remove(permission: str) -> None:
            pc.put(
                f"/roles/{everyone['id']}",
                json={
                    "description": everyone["description"],
                    "permissions": [p for p in original_permissions if p != permission],
                },
            ).raise_for_status()

        yield _remove

        pc.put(
            f"/roles/{everyone['id']}",
            json={"description": everyone["description"], "permissions": original_permissions},
        ).raise_for_status()


@pytest.fixture(scope="session", autouse=True)
async def _grant_config_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as client:
        roles = (await client.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-config")
        existing = (
            await client.get(
                "/role-assignments", params={"principal_id": CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CONFIG_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        # "case" ist ein reserviertes SQL-Schluesselwort - rohe SQL-Strings
        # muessen es selbst quoten (siehe models.py-Kommentar).
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('TRUNCATE "case".case_document_reference, "case".cases CASCADE'))
        await conn.execute(text('TRUNCATE "case".case_archival_config'))
        # Vorgangsnummer (2.5, P15-S3) - beide ebenfalls Singleton-/Zähler-
        # Zeilen, sonst leckt z. B. ein per Test geänderter Format-String in
        # nachfolgende Testläufe (gleiches Vergessen wie ursprünglich bei
        # case_archival_config oben, hier von Anfang an mitgezogen).
        await conn.execute(text('TRUNCATE "case".case_number_config'))
        await conn.execute(text('TRUNCATE "case".case_sequence'))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def manual_task_bpmn() -> str:
    """Prozess mit paralleler Script-/Manual-Task (aus dem offiziellen
    sartography/SpiffWorkflow-Repo, bereits als workflow-service-Fixture
    verifiziert) - erreicht den Endzustand erst nach Abschluss der
    Manual Task, geeignet um `workflow.instance.completed` gezielt nach dem
    Anlegen der Referenzen auszuloesen statt bereits beim Instanzstart."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()
