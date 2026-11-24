"""`GET /document-versions` (P92-S2): versions across documents for service
batch jobs (signature-service's backfill) - service principals only, deleted
documents left out, paged by row id."""

from datetime import UTC, datetime, timedelta

import pytest
from document_service.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "alice"}) as c:
        yield c


def _create(client: TestClient, name: str, content: bytes, content_type: str) -> dict:
    response = client.post(
        "/documents",
        data={"title": name, "created_by": "alice"},
        files={"file": (name, content, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_only_service_principals_may_list(client):
    assert client.get("/document-versions").status_code == 403
    assert (
        client.get(
            "/document-versions", headers={"X-DMS-Principal": "signature-service"}
        ).status_code
        == 200
    )


def test_filters_and_paging(client):
    since = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
    pdf = _create(client, "a.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
    txt = _create(client, "b.txt", b"hallo", "text/plain")
    service = {"X-DMS-Principal": "signature-service"}

    rows = client.get(
        "/document-versions",
        params={"created_from": since, "content_type": "application/pdf"},
        headers=service,
    ).json()
    ids = {r["document_id"] for r in rows}
    assert pdf["id"] in ids and txt["id"] not in ids
    row = next(r for r in rows if r["document_id"] == pdf["id"])
    assert row["version_number"] == 1 and row["filename"] == "a.pdf"

    first = client.get(
        "/document-versions", params={"created_from": since, "limit": 1}, headers=service
    ).json()
    rest = client.get(
        "/document-versions",
        params={"created_from": since, "after_id": first[0]["id"]},
        headers=service,
    ).json()
    assert first[0]["id"] not in {r["id"] for r in rest}

    assert client.delete(f"/documents/{pdf['id']}").status_code in (200, 204)
    after_delete = client.get(
        "/document-versions", params={"created_from": since}, headers=service
    ).json()
    assert pdf["id"] not in {r["document_id"] for r in after_delete}
