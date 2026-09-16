import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

OBJECT_TYPE_SERVICE_URL = Settings().object_type_service_base_url
# Muss mit conftest.py::OBJECT_CONFIG_ADMIN_PRINCIPAL_ID übereinstimmen (dort
# per `_grant_object_config_permission_for_test_setup`-Fixture berechtigt) -
# kein Cross-File-Import von Test-Konstanten, gleiche Projektkonvention wie
# andernorts. Post-Roadmap Phase 38 Session 3: `object-type-service`'s
# `POST`/`DELETE /object-types` now require `admin.object_config` too.
OBJECT_CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "folder-service-test-object-config-admin"}


@pytest.fixture
def client():
    """Post-Roadmap Phase 38 Session 4 (ADR 0149): default `X-DMS-Principal`,
    same pattern as `test_api.py`'s `client` fixture - core folder CRUD now
    requires a valid principal."""
    with TestClient(app, headers={"X-DMS-Principal": "folder-service-tests"}) as c:
        yield c


@pytest.fixture
def object_type_id():
    with httpx.Client(
        base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0, headers=OBJECT_CONFIG_ADMIN_HEADERS
    ) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "Projektordner",
                "applies_to": "folder",
                "attributes": [{"name": "Projektnummer", "type": "string", "required": True}],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


def test_create_folder_with_invalid_attributes_is_rejected(client, object_type_id):
    response = client.post(
        "/folders",
        json={"name": "Projekt X", "created_by": "alice", "object_type_id": object_type_id},
    )
    assert response.status_code == 400
    assert "Projektnummer" in str(response.json()["detail"])


def test_create_folder_with_valid_attributes_succeeds(client, object_type_id):
    response = client.post(
        "/folders",
        json={
            "name": "Projekt X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Projektnummer": "P-001"},
        },
    )
    assert response.status_code == 201


def test_update_folder_attributes_without_move_is_validated(client, object_type_id):
    """Bugfix (P14-S12): eine reine Attributänderung ohne Verschiebung muss
    exakt wie eine Neuanlage gegen den Objekttyp validiert werden - vorher
    griff die Prüfung nur bei einer tatsächlichen Verschiebung (`is_move`)."""
    folder = client.post(
        "/folders",
        json={
            "name": "Projekt X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Projektnummer": "P-001"},
        },
    ).json()

    response = client.patch(f"/folders/{folder['id']}", json={"attributes": {}})

    assert response.status_code == 400
    assert "Projektnummer" in str(response.json()["detail"])


def test_update_folder_attributes_without_move_accepts_valid_attributes(client, object_type_id):
    folder = client.post(
        "/folders",
        json={
            "name": "Projekt X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Projektnummer": "P-001"},
        },
    ).json()

    response = client.patch(
        f"/folders/{folder['id']}", json={"attributes": {"Projektnummer": "P-002"}}
    )

    assert response.status_code == 200
    assert response.json()["attributes"] == {"Projektnummer": "P-002"}


def test_update_folder_name_only_without_move_is_still_validated_against_existing_attributes(
    client, object_type_id
):
    """Ein reines Umbenennen ohne mitgeschickte `attributes` darf die
    bestehenden, bereits gespeicherten Attribute nicht unter den Tisch fallen
    lassen - die Validierung muss gegen `current.attributes` laufen, wenn
    `payload.attributes` nicht gesetzt ist (siehe document-service-Analogie)."""
    folder = client.post(
        "/folders",
        json={
            "name": "Projekt X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Projektnummer": "P-001"},
        },
    ).json()

    response = client.patch(f"/folders/{folder['id']}", json={"name": "Projekt Y"})

    assert response.status_code == 200
    assert response.json()["name"] == "Projekt Y"


def test_update_folder_without_object_type_is_unaffected(client):
    folder = client.post("/folders", json={"name": "Untypisiert", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{folder['id']}", json={"attributes": {"anything": "goes"}})

    assert response.status_code == 200


@pytest.fixture
def top_level_type_id():
    with httpx.Client(
        base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0, headers=OBJECT_CONFIG_ADMIN_HEADERS
    ) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "meinTopLevelOrd",
                "applies_to": "folder",
                "allowed_parent_types": ["$ROOT"],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def second_level_type_id(top_level_type_id):
    with httpx.Client(
        base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0, headers=OBJECT_CONFIG_ADMIN_HEADERS
    ) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "meinSecondLevelOrd",
                "applies_to": "folder",
                "allowed_parent_types": ["meinTopLevelOrd"],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


def test_create_folder_with_root_only_type_at_root_succeeds(client, top_level_type_id):
    response = client.post(
        "/folders", json={"name": "Top", "created_by": "alice", "object_type_id": top_level_type_id}
    )
    assert response.status_code == 201


def test_create_folder_with_root_only_type_under_another_folder_is_rejected(
    client, top_level_type_id
):
    other = client.post("/folders", json={"name": "Anderer", "created_by": "alice"}).json()
    response = client.post(
        "/folders",
        json={
            "name": "Top",
            "created_by": "alice",
            "object_type_id": top_level_type_id,
            "parent_id": other["id"],
        },
    )
    assert response.status_code == 400
    assert "Elternordner" in str(response.json()["detail"])


def test_create_folder_under_correct_parent_class_succeeds(
    client, top_level_type_id, second_level_type_id
):
    top = client.post(
        "/folders", json={"name": "Top", "created_by": "alice", "object_type_id": top_level_type_id}
    ).json()
    response = client.post(
        "/folders",
        json={
            "name": "Second",
            "created_by": "alice",
            "object_type_id": second_level_type_id,
            "parent_id": top["id"],
        },
    )
    assert response.status_code == 201


def test_create_folder_under_wrong_parent_class_is_rejected(client, second_level_type_id):
    wrong_parent = client.post("/folders", json={"name": "Falsch", "created_by": "alice"}).json()
    response = client.post(
        "/folders",
        json={
            "name": "Second",
            "created_by": "alice",
            "object_type_id": second_level_type_id,
            "parent_id": wrong_parent["id"],
        },
    )
    assert response.status_code == 400


def test_move_folder_to_disallowed_parent_is_rejected(
    client, top_level_type_id, second_level_type_id
):
    top = client.post(
        "/folders", json={"name": "Top", "created_by": "alice", "object_type_id": top_level_type_id}
    ).json()
    second = client.post(
        "/folders",
        json={
            "name": "Second",
            "created_by": "alice",
            "object_type_id": second_level_type_id,
            "parent_id": top["id"],
        },
    ).json()
    other_top = client.post("/folders", json={"name": "AndererTop", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{second['id']}", json={"parent_id": other_top["id"]})
    assert response.status_code == 400


def test_move_folder_to_allowed_parent_succeeds(client, top_level_type_id, second_level_type_id):
    top1 = client.post(
        "/folders",
        json={"name": "Top1", "created_by": "alice", "object_type_id": top_level_type_id},
    ).json()
    top2 = client.post(
        "/folders",
        json={"name": "Top2", "created_by": "alice", "object_type_id": top_level_type_id},
    ).json()
    second = client.post(
        "/folders",
        json={
            "name": "Second",
            "created_by": "alice",
            "object_type_id": second_level_type_id,
            "parent_id": top1["id"],
        },
    ).json()

    response = client.patch(f"/folders/{second['id']}", json={"parent_id": top2["id"]})
    assert response.status_code == 200
    assert response.json()["parent_id"] == top2["id"]


def test_folder_without_object_type_is_unaffected_by_parent_constraints(client):
    response = client.post("/folders", json={"name": "Untypisiert", "created_by": "alice"})
    assert response.status_code == 201
