import httpx
import pytest
from document_service.main import app
from document_service.settings import Settings
from fastapi.testclient import TestClient

settings = Settings()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def real_folder_id():
    with httpx.Client(base_url=settings.folder_service_base_url, timeout=10.0) as fc:
        response = fc.post("/folders", json={"name": "Testordner", "created_by": "alice"})
        response.raise_for_status()
        folder_id = response.json()["id"]
        yield folder_id
        fc.delete(f"/folders/{folder_id}")


@pytest.fixture
def object_type_id():
    with httpx.Client(base_url=settings.object_type_service_base_url, timeout=10.0) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "Rechnung-Doc-Test",
                "applies_to": "document",
                "attributes": [{"name": "Rechnungsnummer", "type": "string", "required": True}],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


def test_create_document_with_unknown_folder_returns_400(client):
    response = client.post(
        "/documents",
        data={"title": "X", "created_by": "alice", "folder_id": "does-not-exist"},
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 400


def test_create_document_with_real_folder_succeeds(client, real_folder_id):
    response = client.post(
        "/documents",
        data={"title": "X", "created_by": "alice", "folder_id": real_folder_id},
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["folder_id"] == real_folder_id


def test_create_document_with_invalid_attributes_returns_400(client, object_type_id):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": "{}",
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Rechnungsnummer" in str(response.json()["detail"])


def test_create_document_with_valid_attributes_succeeds(client, object_type_id):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": '{"Rechnungsnummer": "RE-1"}',
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["attributes"] == {"Rechnungsnummer": "RE-1"}


def test_create_document_with_malformed_attributes_json_returns_400(client):
    response = client.post(
        "/documents",
        data={"title": "X", "created_by": "alice", "attributes": "not-json"},
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 400


@pytest.fixture
def folder_type_id():
    with httpx.Client(base_url=settings.object_type_service_base_url, timeout=10.0) as oc:
        response = oc.post(
            "/object-types", json={"name": "Projektordner-Doc-Test", "applies_to": "folder"}
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def restricted_document_type_id(folder_type_id):
    with httpx.Client(base_url=settings.object_type_service_base_url, timeout=10.0) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "Rechnung-Restricted-Doc-Test",
                "applies_to": "document",
                "allowed_parent_types": ["Projektordner-Doc-Test"],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def typed_folder_id(folder_type_id):
    with httpx.Client(base_url=settings.folder_service_base_url, timeout=10.0) as fc:
        response = fc.post(
            "/folders",
            json={
                "name": "Typisierter Ordner",
                "created_by": "alice",
                "object_type_id": folder_type_id,
            },
        )
        response.raise_for_status()
        folder_id = response.json()["id"]
        yield folder_id
        fc.delete(f"/folders/{folder_id}")


def test_create_document_under_correct_parent_class_succeeds(
    client, restricted_document_type_id, typed_folder_id
):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": restricted_document_type_id,
            "folder_id": typed_folder_id,
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201


def test_create_document_under_wrong_parent_class_is_rejected(
    client, restricted_document_type_id, real_folder_id
):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": restricted_document_type_id,
            "folder_id": real_folder_id,
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 400
    assert "Elternordner" in str(response.json()["detail"])


def test_create_document_without_folder_is_rejected_when_root_not_allowed(
    client, restricted_document_type_id
):
    response = client.post(
        "/documents",
        data={"title": "X", "created_by": "alice", "object_type_id": restricted_document_type_id},
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 400


@pytest.fixture
def kennzeichen_object_type_id():
    with httpx.Client(base_url=settings.object_type_service_base_url, timeout=10.0) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "Rechnung-Kennzeichen-Doc-Test",
                "applies_to": "document",
                "kennzeichen_format": "{YYYY}-{Laufende_Nummer}",
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


def test_create_document_assigns_generated_kennzeichen(client, kennzeichen_object_type_id):
    response = client.post(
        "/documents",
        data={"title": "X", "created_by": "alice", "object_type_id": kennzeichen_object_type_id},
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["attributes"]["Kennzeichen"].endswith("-001")


def test_create_document_ignores_client_supplied_kennzeichen_when_generator_configured(
    client, kennzeichen_object_type_id
):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": kennzeichen_object_type_id,
            "attributes": '{"Kennzeichen": "FAKE"}',
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["attributes"]["Kennzeichen"] != "FAKE"


def test_create_document_without_configured_generator_has_no_kennzeichen(client, object_type_id):
    response = client.post(
        "/documents",
        data={
            "title": "X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": '{"Rechnungsnummer": "RE-1"}',
        },
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 201
    assert "Kennzeichen" not in response.json()["attributes"]
