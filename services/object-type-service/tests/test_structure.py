"""Enforced filing structure, parent side (P91-S2a, ADR 0255)."""

import pytest
from fastapi.testclient import TestClient
from object_type_service.main import app

OBJECT_CONFIG_PRINCIPAL_ID = "object-type-service-tests"


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": OBJECT_CONFIG_PRINCIPAL_ID}) as c:
        yield c


def _create(client, **payload) -> dict:
    response = client.post("/object-types", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def chain(client):
    """root -> Abteilung -> Team -> Vorgang -> Akte (documents)."""
    akte = _create(client, name="Akte", applies_to="document")
    brief = _create(client, name="Brief", applies_to="document")
    vorgang = _create(
        client,
        name="Vorgang",
        applies_to="folder",
        allowed_document_types=["Akte"],
        default_document_type="Akte",
    )
    team = _create(
        client,
        name="Team",
        applies_to="folder",
        allowed_child_folder_types=["Vorgang"],
        allowed_document_types=[],
        default_child_folder_type="Vorgang",
    )
    abteilung = _create(
        client,
        name="Abteilung",
        applies_to="folder",
        allowed_parent_types=["$ROOT"],
        allowed_child_folder_types=["Team"],
    )
    client.put(
        "/structure-config",
        json={"root_allowed_folder_types": ["Abteilung"], "enforcement": "enforce"},
    ).raise_for_status()
    return {
        "akte": akte,
        "brief": brief,
        "vorgang": vorgang,
        "team": team,
        "abteilung": abteilung,
    }


def _check(client, **body) -> dict:
    response = client.post("/structure/check", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_fields_are_stored_and_returned(client, chain):
    team = client.get(f"/object-types/{chain['team']['id']}").json()
    assert team["allowed_child_folder_types"] == ["Vorgang"]
    assert team["allowed_document_types"] == []
    assert team["default_child_folder_type"] == "Vorgang"
    assert team["children_from_template_only"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "X", "applies_to": "document", "allowed_document_types": []},
        {"name": "X", "applies_to": "folder", "allowed_child_folder_types": ["Unbekannt"]},
        {"name": "X", "applies_to": "folder", "allowed_child_folder_types": ["Akte"]},
        {"name": "X", "applies_to": "folder", "allowed_document_types": ["Vorgang"]},
        {
            "name": "X",
            "applies_to": "folder",
            "allowed_document_types": ["Akte"],
            "default_document_type": "Brief",
        },
        {"name": "X", "applies_to": "document", "children_from_template_only": True},
    ],
)
def test_invalid_structure_fields_return_422(client, chain, payload):
    assert client.post("/object-types", json=payload).status_code == 422


def test_update_without_the_fields_keeps_them_and_null_opens_again(client, chain):
    team_id = chain["team"]["id"]
    client.put(f"/object-types/{team_id}", json={"attributes": []}).raise_for_status()
    assert client.get(f"/object-types/{team_id}").json()["allowed_child_folder_types"] == [
        "Vorgang"
    ]
    response = client.put(
        f"/object-types/{team_id}",
        json={"allowed_child_folder_types": None, "default_child_folder_type": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["allowed_child_folder_types"] is None


def test_check_with_enforcement_off_checks_nothing(client, chain):
    client.put("/structure-config", json={"enforcement": "off"}).raise_for_status()
    result = _check(client, parent_is_root=True, child_kind="folder")
    assert result == {"enforcement": "off", "errors": [], "default_object_type_id": None}


def test_root_admits_only_its_list(client, chain):
    assert (
        _check(
            client,
            parent_is_root=True,
            child_kind="folder",
            child_object_type_id=chain["abteilung"]["id"],
        )["errors"]
        == []
    )
    untyped = _check(client, parent_is_root=True, child_kind="folder")
    assert "Ablage (oberste Ebene)" in untyped["errors"][0]
    team = _check(
        client, parent_is_root=True, child_kind="folder", child_object_type_id=chain["team"]["id"]
    )
    assert team["errors"]
    # Documents at the root stay open (no list for them).
    assert _check(client, parent_is_root=True, child_kind="document")["errors"] == []


def test_both_sides_must_agree(client, chain):
    # Team lists Vorgang, but Abteilung's own rule only allows the root.
    client.put(
        f"/object-types/{chain['team']['id']}",
        json={"allowed_child_folder_types": ["Vorgang", "Abteilung"]},
    ).raise_for_status()
    result = _check(
        client,
        parent_object_type_id=chain["team"]["id"],
        child_kind="folder",
        child_object_type_id=chain["abteilung"]["id"],
    )
    assert any("Elternklassen" in error for error in result["errors"])


def test_untyped_child_takes_the_parents_default(client, chain):
    result = _check(client, parent_object_type_id=chain["team"]["id"], child_kind="folder")
    assert result["errors"] == []
    assert result["default_object_type_id"] == chain["vorgang"]["id"]
    doc = _check(client, parent_object_type_id=chain["vorgang"]["id"], child_kind="document")
    assert doc["default_object_type_id"] == chain["akte"]["id"]
    # No default and a list: refused.
    refused = _check(client, parent_object_type_id=chain["abteilung"]["id"], child_kind="folder")
    assert refused["errors"] and refused["default_object_type_id"] is None


def test_documents_below_a_folder_class(client, chain):
    assert _check(
        client,
        parent_object_type_id=chain["team"]["id"],
        child_kind="document",
        child_object_type_id=chain["akte"]["id"],
    )["errors"] == ["Unter „Team“ sind keine Dokumente erlaubt"]
    assert _check(
        client,
        parent_object_type_id=chain["vorgang"]["id"],
        child_kind="document",
        child_object_type_id=chain["brief"]["id"],
    )["errors"]


def test_template_only_class(client, chain):
    client.put(
        f"/object-types/{chain['team']['id']}", json={"children_from_template_only": True}
    ).raise_for_status()
    body = {
        "parent_object_type_id": chain["team"]["id"],
        "child_kind": "folder",
        "child_object_type_id": chain["vorgang"]["id"],
    }
    assert "Strukturvorlage" in _check(client, **body)["errors"][0]
    assert _check(client, **body, via_template=True)["errors"] == []


def test_untyped_parent_is_open(client, chain):
    assert _check(client, child_kind="folder")["errors"] == []


def test_allowed_children_for_forms(client, chain):
    root = client.get("/structure/allowed-children", params={"parent": "root"}).json()
    assert [t["name"] for t in root["folder_types"]] == ["Abteilung"]
    assert root["untyped_folders"] is False
    team = client.get(
        "/structure/allowed-children", params={"parent_object_type_id": chain["team"]["id"]}
    ).json()
    assert [t["name"] for t in team["folder_types"]] == ["Vorgang"]
    assert team["document_types"] == []
    assert team["default_child_folder_type"] == "Vorgang"


def test_structure_config_is_gated_and_validated(client, chain):
    denied = client.put(
        "/structure-config", json={"enforcement": "off"}, headers={"X-DMS-Principal": "someone"}
    )
    assert denied.status_code == 403
    bad = client.put("/structure-config", json={"root_allowed_folder_types": ["Akte"]})
    assert bad.status_code == 422
    assert client.put("/structure-config", json={"enforcement": "loud"}).status_code == 422
    # Partial: the root list stays when only the mode changes.
    client.put("/structure-config", json={"enforcement": "report"}).raise_for_status()
    config = client.get("/structure-config").json()
    assert config["enforcement"] == "report"
    assert config["root_allowed_folder_types"] == ["Abteilung"]
