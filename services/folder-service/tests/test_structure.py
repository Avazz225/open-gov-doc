"""Enforced filing structure in folder-service (P91-S2a, ADR 0255).

Runs against the live object-type-service. The fixture saves the
installation's structure config and puts it back afterwards, so the dev
stack is never left in "enforce" mode."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

settings = Settings()
OBJECT_CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "folder-service-test-object-config-admin"}


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "folder-service-tests"}) as c:
        yield c


@pytest.fixture
def structure():
    """Abteilung (root only) -> Team -> Vorgang (default below Team, with a
    required attribute), enforcement "enforce"."""
    suffix = uuid.uuid4().hex[:8]
    names = {key: f"{key}-{suffix}" for key in ("Abteilung", "Team", "Vorgang")}
    with httpx.Client(
        base_url=settings.object_type_service_base_url,
        timeout=10.0,
        headers=OBJECT_CONFIG_ADMIN_HEADERS,
    ) as oc:
        saved = oc.get("/structure-config").json()
        created = []

        def make(**payload):
            response = oc.post("/object-types", json={"applies_to": "folder", **payload})
            response.raise_for_status()
            created.append(response.json()["id"])
            return response.json()

        try:
            vorgang = make(
                name=names["Vorgang"],
                attributes=[{"name": "Aktenzeichen", "type": "string", "required": True}],
            )
            team = make(
                name=names["Team"],
                allowed_child_folder_types=[names["Vorgang"]],
                default_child_folder_type=names["Vorgang"],
            )
            abteilung = make(
                name=names["Abteilung"],
                allowed_parent_types=["$ROOT"],
                allowed_child_folder_types=[names["Team"]],
            )
            oc.put(
                "/structure-config",
                json={
                    "root_allowed_folder_types": [names["Abteilung"]],
                    "enforcement": "enforce",
                },
            ).raise_for_status()
            yield {
                "oc": oc,
                "vorgang": vorgang,
                "team": team,
                "abteilung": abteilung,
            }
        finally:
            oc.put(
                "/structure-config",
                json={
                    "root_allowed_folder_types": saved["root_allowed_folder_types"],
                    "root_allowed_document_types": saved["root_allowed_document_types"],
                    "enforcement": saved["enforcement"],
                },
            )
            for type_id in reversed(created):
                oc.delete(f"/object-types/{type_id}")


def _folder(client, **body):
    return client.post("/folders", json={"parent_id": "root", **body})


def _abteilung_and_team(client, structure):
    abteilung = _folder(client, name="Bauamt", object_type_id=structure["abteilung"]["id"])
    assert abteilung.status_code == 201, abteilung.text
    team = _folder(
        client,
        name="Team Hochbau",
        parent_id=abteilung.json()["id"],
        object_type_id=structure["team"]["id"],
    )
    assert team.status_code == 201, team.text
    return abteilung.json(), team.json()


def test_root_admits_only_its_list(client, structure):
    refused = _folder(client, name="Lose Ablage")
    assert refused.status_code == 422
    assert "ohne Objekttyp" in refused.json()["detail"]["errors"][0]
    _abteilung_and_team(client, structure)


def test_untyped_folder_takes_the_default_without_its_required_attributes(client, structure):
    _, team = _abteilung_and_team(client, structure)
    created = _folder(client, name="Bauantrag 12", parent_id=team["id"])
    assert created.status_code == 201, created.text
    assert created.json()["object_type_id"] == structure["vorgang"]["id"]
    assert created.json()["attributes"] == {}


def test_a_listed_parent_refuses_other_classes(client, structure):
    abteilung, _ = _abteilung_and_team(client, structure)
    refused = _folder(
        client,
        name="Vorgang direkt",
        parent_id=abteilung["id"],
        object_type_id=structure["vorgang"]["id"],
        attributes={"Aktenzeichen": "AZ-1"},
    )
    assert refused.status_code == 422
    assert "nur Ordner dieser Klassen" in refused.json()["detail"]["errors"][0]


def test_move_is_checked_against_the_new_parent(client, structure):
    abteilung, team = _abteilung_and_team(client, structure)
    vorgang = _folder(client, name="Bauantrag 13", parent_id=team["id"]).json()
    moved = client.patch(f"/folders/{vorgang['id']}", json={"parent_id": abteilung["id"]})
    assert moved.status_code == 422


def test_report_mode_lets_violations_through(client, structure):
    structure["oc"].put("/structure-config", json={"enforcement": "report"}).raise_for_status()
    assert _folder(client, name="Lose Ablage").status_code == 201


def test_teamspace_roots_are_exempt(client, structure):
    response = client.post(
        "/folders",
        json={"name": "Teamspace X", "parent_id": "root"},
        headers={"X-DMS-Principal": "teamspace-service", "X-DMS-On-Behalf-Of": "alice"},
    )
    assert response.status_code == 201, response.text


def test_template_only_class_and_template_apply(client, structure):
    structure["oc"].put(
        f"/object-types/{structure['team']['id']}",
        json={"children_from_template_only": True},
    ).raise_for_status()
    _, team = _abteilung_and_team(client, structure)
    refused = _folder(client, name="Bauantrag 14", parent_id=team["id"])
    assert refused.status_code == 422
    assert "Strukturvorlage" in refused.json()["detail"]["errors"][0]

    # A template whose root node is a Vorgang: captured from a Vorgang folder
    # created while the structure is off.
    structure["oc"].put("/structure-config", json={"enforcement": "off"}).raise_for_status()
    source = _folder(
        client,
        name="Vorgangsakte",
        object_type_id=structure["vorgang"]["id"],
        attributes={"Aktenzeichen": "AZ-0"},
    ).json()
    _folder(client, name="Unterlagen", parent_id=source["id"])
    untyped_source = _folder(client, name="Ohne Typ").json()
    template = client.post(
        "/folder-templates",
        json={"source_folder_id": source["id"], "name": "Vorgangsakte"},
        headers={"X-DMS-Principal": "folder-service-test-object-config-admin"},
    ).json()
    untyped_template = client.post(
        "/folder-templates",
        json={"source_folder_id": untyped_source["id"], "name": "Ohne Typ"},
        headers={"X-DMS-Principal": "folder-service-test-object-config-admin"},
    ).json()
    structure["oc"].put("/structure-config", json={"enforcement": "enforce"}).raise_for_status()

    applied = client.post(
        f"/folder-templates/{template['id']}/apply", json={"target_parent_id": team["id"]}
    )
    assert applied.status_code in (200, 201), applied.text
    assert applied.json()["root_folder"]["object_type_id"] == structure["vorgang"]["id"]

    refused_template = client.post(
        f"/folder-templates/{untyped_template['id']}/apply", json={"target_parent_id": team["id"]}
    )
    assert refused_template.status_code == 422
    assert "Ohne Typ" in refused_template.json()["detail"]["errors"][0]
