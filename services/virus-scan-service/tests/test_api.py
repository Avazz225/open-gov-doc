import pytest
from fastapi.testclient import TestClient
from virus_scan_service.engines.eicar_engine import EICAR_SIGNATURE
from virus_scan_service.main import app


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
