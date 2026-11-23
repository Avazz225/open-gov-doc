"""Document conformity report and bulk retype (P91-S2b, ADR 0255).

Runs against the live object-type- and folder-service; the fixture restores
the installation's structure config afterwards."""

import uuid

import httpx
import pytest
from document_service.main import app
from document_service.settings import Settings
from fastapi.testclient import TestClient

settings = Settings()
ADMIN = {"X-DMS-Principal": "document-service-test-object-config-admin"}


@pytest.fixture
def structure():
    """Vorgang (folder) admits only Akte (required attribute); mode "off"."""
    suffix = uuid.uuid4().hex[:8]
    with (
        httpx.Client(
            base_url=settings.object_type_service_base_url, timeout=10.0, headers=ADMIN
        ) as oc,
        httpx.Client(
            base_url=settings.folder_service_base_url,
            timeout=10.0,
            headers={"X-DMS-Principal": "alice"},
        ) as fc,
    ):
        saved = oc.get("/structure-config").json()
        created: list[int] = []
        folder_id = None

        def make(**payload):
            response = oc.post("/object-types", json=payload)
            response.raise_for_status()
            created.append(response.json()["id"])
            return response.json()

        try:
            akte = make(
                name=f"Akte-{suffix}",
                applies_to="document",
                attributes=[{"name": "Aktenzeichen", "type": "string", "required": True}],
            )
            brief = make(name=f"Brief-{suffix}", applies_to="document")
            vorgang = make(
                name=f"Vorgang-{suffix}", applies_to="folder", allowed_document_types=[akte["name"]]
            )
            folder = fc.post(
                "/folders", json={"name": f"Vorgang {suffix}", "object_type_id": vorgang["id"]}
            )
            folder.raise_for_status()
            folder_id = folder.json()["id"]
            oc.put("/structure-config", json={"enforcement": "off"}).raise_for_status()
            yield {"akte": akte, "brief": brief, "folder_id": folder_id}
        finally:
            oc.put(
                "/structure-config",
                json={
                    "root_allowed_folder_types": saved["root_allowed_folder_types"],
                    "root_allowed_document_types": saved["root_allowed_document_types"],
                    "enforcement": saved["enforcement"],
                },
            )
            if folder_id:
                fc.delete(f"/folders/{folder_id}")
            for type_id in reversed(created):
                oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def client(structure):
    with TestClient(app, headers={"X-DMS-Principal": "alice"}) as c:
        yield c


def _upload(client, **data) -> dict:
    response = client.post(
        "/documents",
        data={"title": "Bescheid", **data},
        files={"file": ("b.txt", b"Inhalt", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _problems_of(client, document_id) -> list[str]:
    response = client.get("/structure/conformity", headers=ADMIN)
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        if item["id"] == document_id:
            return [p["kind"] for p in item["problems"]]
    return []


def test_report_and_retype(client, structure):
    untyped = _upload(client, folder_id=structure["folder_id"])
    brief = _upload(
        client, folder_id=structure["folder_id"], object_type_id=structure["brief"]["id"]
    )
    assert _problems_of(client, untyped["id"]) == ["placement"]
    assert _problems_of(client, brief["id"]) == ["placement"]

    response = client.post(
        "/documents/retype",
        json={
            "document_ids": [untyped["id"], "does-not-exist"],
            "object_type_id": structure["akte"]["id"],
        },
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == [untyped["id"]]
    assert [f["id"] for f in response.json()["failed"]] == ["does-not-exist"]
    # In place now, but the required attribute is still owed.
    assert _problems_of(client, untyped["id"]) == ["missing_required"]

    refused = client.post(
        "/documents/retype",
        json={"document_ids": [untyped["id"]], "object_type_id": structure["brief"]["id"]},
        headers=ADMIN,
    ).json()
    assert refused["updated"] == []
    assert "nur Dokumente dieser Klassen" in refused["failed"][0]["errors"][0]


def test_report_and_retype_are_admin_only(client, structure):
    assert client.get("/structure/conformity").status_code == 403
    assert (
        client.post(
            "/documents/retype", json={"document_ids": [], "object_type_id": None}
        ).status_code
        == 403
    )
    folder_type = client.post(
        "/documents/retype",
        json={"document_ids": [], "object_type_id": 999999},
        headers=ADMIN,
    )
    assert folder_type.status_code == 422
