"""Enforced filing structure in document-service (P91-S2a, ADR 0255).

Runs against the live object-type- and folder-service. The fixture saves the
installation's structure config and puts it back afterwards."""

import uuid

import httpx
import pytest
from document_service.main import app
from document_service.settings import Settings
from fastapi.testclient import TestClient

settings = Settings()
OBJECT_CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-object-config-admin"}


@pytest.fixture
def structure():
    """Vorgang (folder) admits only Akte (default, with a required
    attribute); the root admits no documents; enforcement "enforce"."""
    suffix = uuid.uuid4().hex[:8]
    with (
        httpx.Client(
            base_url=settings.object_type_service_base_url,
            timeout=10.0,
            headers=OBJECT_CONFIG_ADMIN_HEADERS,
        ) as oc,
        httpx.Client(
            base_url=settings.folder_service_base_url,
            timeout=10.0,
            headers={"X-DMS-Principal": "document-service-test-folder-setup"},
        ) as fc,
    ):
        saved = oc.get("/structure-config").json()
        created_types: list[int] = []
        folder_id = None

        def make(**payload):
            response = oc.post("/object-types", json=payload)
            response.raise_for_status()
            created_types.append(response.json()["id"])
            return response.json()

        try:
            akte = make(
                name=f"Akte-{suffix}",
                applies_to="document",
                attributes=[{"name": "Aktenzeichen", "type": "string", "required": True}],
            )
            brief = make(name=f"Brief-{suffix}", applies_to="document")
            vorgang = make(
                name=f"Vorgang-{suffix}",
                applies_to="folder",
                allowed_document_types=[akte["name"]],
                default_document_type=akte["name"],
            )
            folder = fc.post(
                "/folders",
                json={"name": f"Vorgang {suffix}", "object_type_id": vorgang["id"]},
                headers={"X-DMS-Principal": "alice"},
            )
            folder.raise_for_status()
            folder_id = folder.json()["id"]
            oc.put(
                "/structure-config",
                json={"root_allowed_document_types": [], "enforcement": "enforce"},
            ).raise_for_status()
            yield {"oc": oc, "akte": akte, "brief": brief, "folder_id": folder_id}
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
            for type_id in reversed(created_types):
                oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def client(structure):
    # Created after the structure is switched on: the client caches "off".
    with TestClient(app, headers={"X-DMS-Principal": "alice"}) as c:
        yield c


def _upload(client, *, headers=None, **data):
    return client.post(
        "/documents",
        data={"title": "Bescheid", **data},
        files={"file": ("b.txt", b"Inhalt", "text/plain")},
        headers=headers or {},
    )


def test_untyped_upload_takes_the_default_without_its_required_attributes(client, structure):
    response = _upload(client, folder_id=structure["folder_id"])
    assert response.status_code == 201, response.text
    assert response.json()["object_type_id"] == structure["akte"]["id"]


def test_unlisted_type_is_refused(client, structure):
    response = _upload(
        client, folder_id=structure["folder_id"], object_type_id=structure["brief"]["id"]
    )
    assert response.status_code == 422
    assert "nur Dokumente dieser Klassen" in response.json()["detail"]["errors"][0]


def test_root_admits_no_documents_except_restores(client, structure):
    refused = _upload(client)
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"] == [
        "Unter „Ablage (oberste Ebene)“ sind keine Dokumente erlaubt"
    ]
    restored = _upload(client, headers={"X-DMS-Principal": "archival-service"})
    assert restored.status_code == 201, restored.text


def test_move_is_checked(client, structure):
    document = _upload(client, folder_id=structure["folder_id"]).json()
    moved = client.patch(f"/documents/{document['id']}", json={"folder_id": "root"})
    assert moved.status_code == 422


def test_report_mode_lets_violations_through(client, structure):
    structure["oc"].put("/structure-config", json={"enforcement": "report"}).raise_for_status()
    assert _upload(client).status_code == 201
