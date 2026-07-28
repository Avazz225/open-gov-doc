import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

OBJECT_TYPE_SERVICE_URL = Settings().object_type_service_base_url


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def object_type_id():
    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0) as oc:
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


@pytest.fixture
def top_level_type_id():
    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0) as oc:
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
    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0) as oc:
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
