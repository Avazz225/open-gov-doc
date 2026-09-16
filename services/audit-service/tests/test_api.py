import os

import httpx
import pytest
from audit_service.main import app
from fastapi.testclient import TestClient

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")

AUDIT_TEST_PRINCIPAL_ID = "audit-service-tests"
# Post-Roadmap Phase 19 Session 6 (ADR 0071): `PUT /roles/{id}` requires
# `admin.user_management` since that session - separate test principal for
# `everyone_role_without` below (see `_grant_role_admin_permission`).
ROLE_ADMIN_PRINCIPAL_ID = "audit-service-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_role_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
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
def client():
    """First HTTP-API-level test file for this service - `GET /events`/
    `.../verify` previously had NO permission check at all (Post-Roadmap
    Phase 38 Session 2, see docs/services/audit-service.md "Open Points").
    `permission_client` stays UNMOCKED (a real call against the running
    permission-service, same "no mocking of sibling services" philosophy as
    reporting-service/archival-service) - the `TestClient` therefore
    carries a `X-DMS-Principal` header by default (the "everyone" group
    grants `audit.read` to every authenticated principal, no role setup
    needed for the positive case). Individual tests can override the header
    via `headers={"X-DMS-Principal": ""}` to exercise the negative case."""
    with TestClient(app, headers={"X-DMS-Principal": AUDIT_TEST_PRINCIPAL_ID}) as c:
        yield c


@pytest.fixture
def everyone_role_without():
    """Temporarily removes one permission from the seeded "everyone" role to
    prove the negative path (missing permission -> 403) - same pattern as
    archival-service/reporting-service (duplicated, not shared - project
    convention). `PUT /roles/{id}` additionally requires
    `admin.user_management` since Post-Roadmap Phase 19 Session 6
    (ADR 0071)."""
    role_management_headers = {"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID}
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
                headers=role_management_headers,
            ).raise_for_status()

        yield _remove

        pc.put(
            f"/roles/{everyone['id']}",
            json={"description": everyone["description"], "permissions": original_permissions},
            headers=role_management_headers,
        ).raise_for_status()


def test_list_events_without_principal_header_is_401(client):
    response = client.get("/events", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_list_events_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("audit.read")
    response = client.get("/events")
    assert response.status_code == 403


def test_list_events_with_everyone_permission_is_200(client):
    response = client.get("/events")
    assert response.status_code == 200


def test_verify_chain_without_principal_header_is_401(client):
    response = client.get("/events/verify", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_verify_chain_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("audit.read")
    response = client.get("/events/verify")
    assert response.status_code == 403


def test_verify_chain_with_everyone_permission_is_200(client):
    response = client.get("/events/verify")
    assert response.status_code == 200
