import pytest
from fastapi.testclient import TestClient
from object_type_service.main import app

RECHNUNG_PAYLOAD = {
    "name": "Rechnung",
    "applies_to": "document",
    "attributes": [
        {"name": "Rechnungsnummer", "type": "string", "required": True, "pattern": r"RE-\d{6}"},
        {"name": "Betrag", "type": "decimal", "required": True},
    ],
    "conditions": [{"if": "Betrag > 10000", "then": "require:Kostenstelle"}],
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "object-type-service"


def test_create_and_get(client):
    create_response = client.post("/object-types", json=RECHNUNG_PAYLOAD)
    assert create_response.status_code == 201
    object_type_id = create_response.json()["id"]

    get_response = client.get(f"/object-types/{object_type_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Rechnung"


def test_duplicate_name_returns_409(client):
    client.post("/object-types", json=RECHNUNG_PAYLOAD)
    response = client.post("/object-types", json=RECHNUNG_PAYLOAD)
    assert response.status_code == 409


def test_get_unknown_returns_404(client):
    response = client.get("/object-types/999999")
    assert response.status_code == 404


def test_list_filters_by_applies_to(client):
    client.post("/object-types", json=RECHNUNG_PAYLOAD)
    client.post(
        "/object-types", json={"name": "Projektordner", "applies_to": "folder", "attributes": []}
    )

    response = client.get("/object-types", params={"applies_to": "folder"})
    names = {o["name"] for o in response.json()}
    assert names == {"Projektordner"}


def test_validate_endpoint_reports_errors(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    invalid = client.post(
        f"/object-types/{object_type_id}/validate",
        json={"name": "irrelevant.pdf", "attributes": {}},
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert len(invalid.json()["errors"]) > 0

    valid = client.post(
        f"/object-types/{object_type_id}/validate",
        json={
            "name": "RE-123456.pdf",
            "attributes": {"Rechnungsnummer": "RE-123456", "Betrag": 5},
        },
    )
    assert valid.json() == {"valid": True, "errors": []}


def test_validate_conditional_requirement(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    response = client.post(
        f"/object-types/{object_type_id}/validate",
        json={
            "name": "RE-123456.pdf",
            "attributes": {"Rechnungsnummer": "RE-123456", "Betrag": 50000},
        },
    )
    assert response.json()["valid"] is False
    assert any("Kostenstelle" in e for e in response.json()["errors"])


def test_validate_unknown_object_type_returns_404(client):
    response = client.post("/object-types/999999/validate", json={"name": "x", "attributes": {}})
    assert response.status_code == 404


def test_create_with_allowed_parent_types_referencing_unknown_type_returns_422(client):
    response = client.post(
        "/object-types",
        json={"name": "MeinDoc", "applies_to": "document", "allowed_parent_types": ["Unbekannt"]},
    )
    assert response.status_code == 422


def test_create_with_allowed_parent_types_referencing_document_type_returns_422(client):
    client.post("/object-types", json=RECHNUNG_PAYLOAD)
    response = client.post(
        "/object-types",
        json={
            "name": "AndereRechnung",
            "applies_to": "document",
            "allowed_parent_types": ["Rechnung"],
        },
    )
    assert response.status_code == 422


def test_create_icon_on_document_type_returns_422(client):
    response = client.post(
        "/object-types", json={"name": "MeinDoc2", "applies_to": "document", "icon": "file"}
    )
    assert response.status_code == 422


def test_create_folder_hierarchy_with_root_sentinel_succeeds(client):
    top = client.post(
        "/object-types",
        json={
            "name": "meinTopLevelOrd",
            "applies_to": "folder",
            "allowed_parent_types": ["$ROOT"],
            "icon": "folder-top",
        },
    )
    assert top.status_code == 201
    assert top.json()["allowed_parent_types"] == ["$ROOT"]
    assert top.json()["icon"] == "folder-top"

    second = client.post(
        "/object-types",
        json={
            "name": "meinSecondLevelOrd",
            "applies_to": "folder",
            "allowed_parent_types": ["meinTopLevelOrd"],
        },
    )
    assert second.status_code == 201

    doc = client.post(
        "/object-types",
        json={
            "name": "meinDoc",
            "applies_to": "document",
            "allowed_parent_types": ["meinSecondLevelOrd"],
        },
    )
    assert doc.status_code == 201


def test_validate_endpoint_accepts_placement_under_root(client):
    object_type_id = client.post(
        "/object-types",
        json={"name": "NurAmRoot", "applies_to": "folder", "allowed_parent_types": ["$ROOT"]},
    ).json()["id"]

    at_root = client.post(
        f"/object-types/{object_type_id}/validate",
        json={"name": "x", "attributes": {}, "parent_is_root": True},
    )
    assert at_root.json() == {"valid": True, "errors": []}

    not_at_root = client.post(
        f"/object-types/{object_type_id}/validate",
        json={"name": "x", "attributes": {}, "parent_is_root": False},
    )
    assert not_at_root.json()["valid"] is False


def test_validate_endpoint_resolves_parent_object_type_id_to_name(client):
    parent_id = client.post(
        "/object-types", json={"name": "meinTopLevelOrd2", "applies_to": "folder"}
    ).json()["id"]
    child_id = client.post(
        "/object-types",
        json={
            "name": "meinSecondLevelOrd2",
            "applies_to": "folder",
            "allowed_parent_types": ["meinTopLevelOrd2"],
        },
    ).json()["id"]

    matching = client.post(
        f"/object-types/{child_id}/validate",
        json={"name": "x", "attributes": {}, "parent_object_type_id": parent_id},
    )
    assert matching.json() == {"valid": True, "errors": []}

    other_id = client.post(
        "/object-types", json={"name": "AndereOrdnerklasse", "applies_to": "folder"}
    ).json()["id"]
    mismatching = client.post(
        f"/object-types/{child_id}/validate",
        json={"name": "x", "attributes": {}, "parent_object_type_id": other_id},
    )
    assert mismatching.json()["valid"] is False


def test_validate_endpoint_untyped_parent_rejected_when_specific_parent_required(client):
    client.post("/object-types", json={"name": "meinTopLevelOrd3", "applies_to": "folder"})
    child_id = client.post(
        "/object-types",
        json={
            "name": "meinSecondLevelOrd3",
            "applies_to": "folder",
            "allowed_parent_types": ["meinTopLevelOrd3"],
        },
    ).json()["id"]

    response = client.post(
        f"/object-types/{child_id}/validate",
        json={"name": "x", "attributes": {}},
    )
    assert response.json()["valid"] is False


def test_update_and_delete(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    update_response = client.put(
        f"/object-types/{object_type_id}",
        json={"attributes": [{"name": "Neu", "type": "string"}], "conditions": []},
    )
    assert update_response.status_code == 200
    assert len(update_response.json()["attributes"]) == 1

    delete_response = client.delete(f"/object-types/{object_type_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/object-types/{object_type_id}").status_code == 404


def test_get_layout_without_override_returns_generated_smart_layout(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    response = client.get(f"/object-types/{object_type_id}/layouts/display")
    assert response.status_code == 200
    body = response.json()
    assert body["is_custom"] is False
    assert body["responsive_breakpoint_px"] == 600
    assert body["rows"][0]["columns"][0]["attribute"] == "Rechnungsnummer"
    assert body["rows"][0]["columns"][0]["required"] is True


def test_get_layout_unknown_object_type_returns_404(client):
    response = client.get("/object-types/999999/layouts/display")
    assert response.status_code == 404


def test_get_layout_invalid_purpose_returns_422(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]
    response = client.get(f"/object-types/{object_type_id}/layouts/irgendwas")
    assert response.status_code == 422


def test_put_layout_persists_override_and_get_returns_it(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    put_response = client.put(
        f"/object-types/{object_type_id}/layouts/upload",
        json={
            "rows": [
                {"columns": [{"attribute": "Betrag", "label": "Rechnungsbetrag", "required": True}]}
            ],
            "responsive_breakpoint_px": 480,
        },
    )
    assert put_response.status_code == 200
    assert put_response.json()["is_custom"] is True
    assert put_response.json()["responsive_breakpoint_px"] == 480

    get_response = client.get(f"/object-types/{object_type_id}/layouts/upload")
    assert get_response.json()["is_custom"] is True
    assert get_response.json()["rows"][0]["columns"][0]["label"] == "Rechnungsbetrag"

    other_purpose = client.get(f"/object-types/{object_type_id}/layouts/display")
    assert other_purpose.json()["is_custom"] is False


def test_put_layout_referencing_unknown_attribute_returns_422(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    response = client.put(
        f"/object-types/{object_type_id}/layouts/search",
        json={"rows": [{"columns": [{"attribute": "Unbekannt", "label": "Unbekannt"}]}]},
    )
    assert response.status_code == 422


def test_put_layout_unknown_object_type_returns_404(client):
    response = client.put("/object-types/999999/layouts/display", json={"rows": []})
    assert response.status_code == 404


def test_delete_layout_resets_to_generated_default(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]
    client.put(
        f"/object-types/{object_type_id}/layouts/display",
        json={"rows": [{"columns": [{"attribute": "Betrag", "label": "Betrag"}]}]},
    )
    assert client.get(f"/object-types/{object_type_id}/layouts/display").json()["is_custom"] is True

    delete_response = client.delete(f"/object-types/{object_type_id}/layouts/display")
    assert delete_response.status_code == 204

    reset = client.get(f"/object-types/{object_type_id}/layouts/display")
    assert reset.json()["is_custom"] is False


def test_delete_layout_unknown_object_type_returns_404(client):
    response = client.delete("/object-types/999999/layouts/display")
    assert response.status_code == 404


def test_create_with_kennzeichen_format_on_folder_type_returns_422(client):
    response = client.post(
        "/object-types",
        json={
            "name": "OrdnerMitKennzeichenAPI",
            "applies_to": "folder",
            "kennzeichen_format": "{Laufende_Nummer}",
        },
    )
    assert response.status_code == 422


def test_create_with_kennzeichen_format_missing_laufende_nummer_returns_422(client):
    response = client.post(
        "/object-types",
        json={
            "name": "MeinDocOhneNummerAPI",
            "applies_to": "document",
            "kennzeichen_format": "{YYYY}",
        },
    )
    assert response.status_code == 422


def test_next_kennzeichen_returns_incrementing_formatted_values(client):
    object_type_id = client.post(
        "/object-types",
        json={
            "name": "RechnungMitKennzeichenAPI",
            "applies_to": "document",
            "kennzeichen_format": "{YYYY}-{Laufende_Nummer}",
        },
    ).json()["id"]

    first = client.post(f"/object-types/{object_type_id}/next-kennzeichen")
    second = client.post(f"/object-types/{object_type_id}/next-kennzeichen")
    assert first.status_code == 200
    assert second.status_code == 200
    first_kennzeichen = first.json()["kennzeichen"]
    second_kennzeichen = second.json()["kennzeichen"]
    assert first_kennzeichen != second_kennzeichen
    assert first_kennzeichen.endswith("-001")
    assert second_kennzeichen.endswith("-002")


def test_next_kennzeichen_without_configured_format_returns_404(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]
    response = client.post(f"/object-types/{object_type_id}/next-kennzeichen")
    assert response.status_code == 404


def test_next_kennzeichen_unknown_object_type_returns_404(client):
    response = client.post("/object-types/999999/next-kennzeichen")
    assert response.status_code == 404


def test_get_kennzeichen_config_defaults_to_show_before_filename(client):
    response = client.get("/kennzeichen-config")
    assert response.status_code == 200
    assert response.json()["show_before_filename"] is True


def test_put_kennzeichen_config_persists(client):
    put_response = client.put("/kennzeichen-config", json={"show_before_filename": False})
    assert put_response.status_code == 200
    assert put_response.json()["show_before_filename"] is False

    get_response = client.get("/kennzeichen-config")
    assert get_response.json()["show_before_filename"] is False
