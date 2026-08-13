import os

import httpx
import pytest
from fastapi.testclient import TestClient
from virus_scan_service.engines.eicar_engine import EICAR_SIGNATURE
from virus_scan_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - ersetzt das bisherige
# reine `X-DMS-Roles`-Gate durch eine echte permission-service-Prüfung
# (`admin.quarantine`, Rolle "domain-admin-virus-scan"). Bewusst NICHT in
# der "everyone"-Gruppe (siehe `main.py::_require_quarantine_permission`) -
# jeder Principal OHNE explizite Zuweisung ist automatisch der Negativfall,
# kein `everyone_role_without`-Fixture nötig.
QUARANTINE_ADMIN_PRINCIPAL_ID = "virus-scan-service-test-quarantine-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_quarantine_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-virus-scan")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": QUARANTINE_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": QUARANTINE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def scan(client, *, content=b"Hallo Welt", filename="vertrag.pdf", **extra):
    files = {"file": (filename, content, "application/pdf")}
    return client.post("/scan", data=extra, files=files)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "virus-scan-service"


def test_scan_reports_clean_for_harmless_content(client):
    response = scan(client)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "clean"
    assert body["threat_name"] is None
    assert body["quarantine_object_key"] is None


def test_scan_detects_eicar_and_quarantines_it(client):
    response = scan(client, content=EICAR_SIGNATURE, created_by="alice", document_id="doc-1")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "infected"
    assert body["threat_name"] == "Eicar-Test-Signature"
    assert body["quarantine_object_key"] == f"quarantine/{body['id']}"
    assert body["document_id"] == "doc-1"
    assert body["created_by"] == "alice"


def test_get_scan_result_by_id(client):
    created = scan(client).json()

    response = client.get(f"/scans/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_unknown_scan_returns_404(client):
    response = client.get("/scans/does-not-exist")
    assert response.status_code == 404


def test_list_scans_filters_by_document_id(client):
    scan(client, document_id="doc-a")
    scan(client, document_id="doc-b")

    response = client.get("/scans", params={"document_id": "doc-a"})

    assert response.status_code == 200
    assert all(item["document_id"] == "doc-a" for item in response.json())
    assert len(response.json()) == 1


# `X-DMS-Roles: dms-admin` bleibt nötig, obwohl virus-scan-service selbst
# seit dieser Session nicht mehr darauf prüft - `document-service`s eigenes,
# UNABHÄNGIGES `quarantine_release_admin_role`-Gate (`_has_quarantine_
# release_role`, `POST /documents/from-quarantine-release`) verlangt es
# weiterhin und wird von `release_scan` unverändert durchgereicht.
ADMIN_HEADERS = {"X-DMS-Principal": QUARANTINE_ADMIN_PRINCIPAL_ID, "X-DMS-Roles": "dms-admin"}
NO_PERMISSION_HEADERS = {"X-DMS-Principal": "no-quarantine-permission-user"}


def test_list_infected_scans_requires_principal(client):
    scan(client, content=EICAR_SIGNATURE)

    response = client.get("/scans", params={"status": "infected"})

    assert response.status_code == 401


def test_list_infected_scans_requires_quarantine_role(client):
    scan(client, content=EICAR_SIGNATURE)

    response = client.get("/scans", params={"status": "infected"}, headers=NO_PERMISSION_HEADERS)

    assert response.status_code == 403


def test_list_infected_scans_with_role_returns_only_infected(client):
    scan(client)
    scan(client, content=EICAR_SIGNATURE)

    response = client.get("/scans", params={"status": "infected"}, headers=ADMIN_HEADERS)

    assert response.status_code == 200
    assert all(item["status"] == "infected" for item in response.json())
    assert len(response.json()) == 1


def test_list_scans_without_status_stays_ungated(client):
    scan(client, content=EICAR_SIGNATURE)

    response = client.get("/scans")

    assert response.status_code == 200


def test_release_unknown_scan_returns_404(client):
    response = client.post(
        "/scans/does-not-exist/release", json={"title": "Freigegeben"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 404


def test_release_requires_quarantine_role(client):
    created = scan(client, content=EICAR_SIGNATURE).json()

    response = client.post(
        f"/scans/{created['id']}/release",
        json={"title": "Freigegeben"},
        headers=NO_PERMISSION_HEADERS,
    )

    assert response.status_code == 403


def test_release_of_clean_scan_returns_409(client):
    created = scan(client).json()

    response = client.post(
        f"/scans/{created['id']}/release", json={"title": "Freigegeben"}, headers=ADMIN_HEADERS
    )

    assert response.status_code == 409


def test_release_creates_document_and_marks_scan_released(client):
    created = scan(client, content=EICAR_SIGNATURE, filename="fehlalarm.txt").json()

    response = client.post(
        f"/scans/{created['id']}/release",
        json={"title": "Fehlalarm freigegeben"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "released"
    assert body["document_id"] is not None
    assert body["resolved_by"] == QUARANTINE_ADMIN_PRINCIPAL_ID
    assert body["resolved_at"] is not None

    # kein zweites Mal freigebbar
    again = client.post(
        f"/scans/{created['id']}/release", json={"title": "Nochmal"}, headers=ADMIN_HEADERS
    )
    assert again.status_code == 409


def test_purge_unknown_scan_returns_404(client):
    response = client.post("/scans/does-not-exist/purge", headers=ADMIN_HEADERS)
    assert response.status_code == 404


def test_purge_requires_quarantine_role(client):
    created = scan(client, content=EICAR_SIGNATURE).json()

    response = client.post(f"/scans/{created['id']}/purge", headers=NO_PERMISSION_HEADERS)

    assert response.status_code == 403


def test_purge_of_clean_scan_returns_409(client):
    created = scan(client).json()

    response = client.post(f"/scans/{created['id']}/purge", headers=ADMIN_HEADERS)

    assert response.status_code == 409


def test_purge_marks_scan_purged(client):
    created = scan(client, content=EICAR_SIGNATURE).json()

    response = client.post(f"/scans/{created['id']}/purge", headers=ADMIN_HEADERS)
    assert response.status_code == 204

    fetched = client.get(f"/scans/{created['id']}").json()
    assert fetched["status"] == "purged"
    assert fetched["resolved_by"] == QUARANTINE_ADMIN_PRINCIPAL_ID

    again = client.post(f"/scans/{created['id']}/purge", headers=ADMIN_HEADERS)
    assert again.status_code == 409
