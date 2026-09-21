import os

import httpx
import pytest

# config-service hat kein eigenes Postgres-Schema (reiner Orchestrator, siehe
# `clients.py`) - Tests laufen wie bei `webdav-connector`/`migration-service`
# gegen den echten, per docker-compose laufenden Container, kein In-Prozess-
# `TestClient(app)` und kein DB-Cleanup nötig.
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")

# Testprinzipal, dem einzelne Tests gezielt die Domain-Admin-Rolle für
# `admin.object_config` zuweisen/entziehen (7.3-Importgate) - siehe
# `_authorized_principal` unten.
IMPORT_TEST_PRINCIPAL = "config-service-import-tests"

# Default-Testprinzipal für `_client()` (test_api.py/test_compare.py) - seit
# Phase 61 Session 3 (ADR 0188) benötigt `GET /config/export`/`POST
# /config/compare` `admin.config_read`, gegatet, siehe
# `_grant_export_read_permission` unten. Kein Cross-File-Import von
# Test-Konstanten - `test_api.py`/`test_compare.py` definieren denselben
# String eigenständig.
CONFIG_SERVICE_TEST_PRINCIPAL = "config-service-tests"


@pytest.fixture(scope="session", autouse=True)
def _grant_export_read_permission():
    """Phase 61 Session 3 (ADR 0188): `GET /config/export`/`POST
    /config/compare` previously had NO permission check at all - grants
    the default `_client()` test principal `admin.config_read` (role
    `domain-admin-config-read`), same idempotent bootstrap pattern as
    every other service's own session-scoped grant fixture."""
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as client:
        roles = client.get("/roles").json()
        role = next(r for r in roles if r["name"] == "domain-admin-config-read")
        existing = client.get(
            "/role-assignments", params={"principal_id": CONFIG_SERVICE_TEST_PRINCIPAL}
        ).json()
        if any(a["role_id"] == role["id"] for a in existing):
            return
        response = client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CONFIG_SERVICE_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture
def authorized_principal():
    """Weist `IMPORT_TEST_PRINCIPAL` vorübergehend `domain-admin-config` zu
    (dieselbe Rolle, die `config-service` sich selbst beim Start gibt, siehe
    `main.py::_ensure_bootstrap_permissions`), damit Tests einen tatsächlich
    autorisierten Import-Aufruf simulieren können - Aufräumen per
    `DELETE /role-assignments/{id}` danach."""
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as client:
        roles = client.get("/roles").json()
        role = next(r for r in roles if r["name"] == "domain-admin-config")
        # Seit P17-S3 (4.3/14.2) liefert `POST /role-assignments` das
        # gegatete `RoleAssignmentActionResult` - ohne aktivierte
        # Genehmigungspflicht (Default) bleibt `role_assignment` sofort
        # gesetzt, siehe permission_service/schemas.py.
        assignment = client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": IMPORT_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).json()["role_assignment"]
        yield IMPORT_TEST_PRINCIPAL
        client.delete(f"/role-assignments/{assignment['id']}")
