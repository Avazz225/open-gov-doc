"""Conformity report, bulk retype, area roots and template management
(P91-S2b, ADR 0255). Runs against the live object-type-service; the
fixture restores the installation's structure config afterwards."""

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

settings = Settings()
ADMIN = {"X-DMS-Principal": "folder-service-test-object-config-admin"}


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "folder-service-tests"}) as c:
        yield c


@pytest.fixture
def types():
    """Abteilung (root only) lists Team; Team has a required attribute. The
    root admits only Abteilung. Mode stays "off": the report doesn't need it."""
    suffix = uuid.uuid4().hex[:8]
    with httpx.Client(
        base_url=settings.object_type_service_base_url, timeout=10.0, headers=ADMIN
    ) as oc:
        saved = oc.get("/structure-config").json()
        created = []

        def make(**payload):
            response = oc.post("/object-types", json={"applies_to": "folder", **payload})
            response.raise_for_status()
            created.append(response.json()["id"])
            return response.json()

        try:
            team = make(
                name=f"Team-{suffix}",
                attributes=[{"name": "Kuerzel", "type": "string", "required": True}],
            )
            abteilung = make(
                name=f"Abteilung-{suffix}",
                allowed_parent_types=["$ROOT"],
                allowed_child_folder_types=[team["name"]],
            )
            oc.put(
                "/structure-config",
                json={"root_allowed_folder_types": [abteilung["name"]], "enforcement": "off"},
            ).raise_for_status()
            yield {"team": team, "abteilung": abteilung}
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


def _folder(client, **body) -> dict:
    response = client.post("/folders", json={"parent_id": "root", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _report(client) -> dict:
    response = client.get("/structure/conformity", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _problems_of(report, folder_id) -> list[str]:
    for item in report["items"]:
        if item["id"] == folder_id:
            return [p["kind"] for p in item["problems"]]
    return []


def test_report_lists_misplaced_and_incomplete_folders_but_not_area_roots(client, types):
    abteilung = _folder(client, name="Bauamt", object_type_id=types["abteilung"]["id"])
    loose = _folder(client, name="Lose Ablage")
    untyped_child = _folder(client, name="Hochbau", parent_id=abteilung["id"])
    area = client.post(
        "/folders",
        json={"name": "Teamspace Y", "parent_id": "root"},
        headers={"X-DMS-Principal": "teamspace-service", "X-DMS-On-Behalf-Of": "alice"},
    ).json()

    report = _report(client)
    assert report["enforcement"] == "off"
    assert _problems_of(report, loose["id"]) == ["placement"]
    assert _problems_of(report, untyped_child["id"]) == ["placement"]
    assert _problems_of(report, abteilung["id"]) == []
    assert _problems_of(report, area["id"]) == []
    item = next(i for i in report["items"] if i["id"] == untyped_child["id"])
    assert item["path"] == "/Bauamt/Hochbau"


def test_report_is_admin_only(client, types):
    assert client.get("/structure/conformity").status_code == 403


def test_retype_fixes_placement_and_reports_missing_attributes(client, types):
    abteilung = _folder(client, name="Bauamt", object_type_id=types["abteilung"]["id"])
    child = _folder(client, name="Hochbau", parent_id=abteilung["id"])
    loose = _folder(client, name="Lose Ablage")

    response = client.post(
        "/folders/retype",
        json={
            "folder_ids": [child["id"], loose["id"], "does-not-exist"],
            "object_type_id": types["team"]["id"],
        },
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == [child["id"]]
    failed = {f["id"]: f["errors"] for f in result["failed"]}
    assert set(failed) == {loose["id"], "does-not-exist"}
    assert any("Ablage (oberste Ebene)" in e for e in failed[loose["id"]])

    assert client.get(f"/folders/{child['id']}").json()["object_type_id"] == types["team"]["id"]
    # Placement is fine now; the required attribute is still owed.
    assert _problems_of(_report(client), child["id"]) == ["missing_required"]


def test_retype_needs_a_folder_class_and_the_admin_capability(client, types):
    folder = _folder(client, name="X")
    assert (
        client.post(
            "/folders/retype", json={"folder_ids": [folder["id"]], "object_type_id": None}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/folders/retype",
            json={"folder_ids": [folder["id"]], "object_type_id": 999999},
            headers=ADMIN,
        ).status_code
        == 422
    )


def test_structure_exempt_marker_is_for_area_owners_only(client, types):
    top = _folder(client, name="Teamspace Z")
    nested = _folder(client, name="Innen", parent_id=top["id"])
    owner = {"X-DMS-Principal": "teamspace-service"}
    assert client.put(f"/folders/{top['id']}/structure-exempt").status_code == 403
    assert client.put(f"/folders/{nested['id']}/structure-exempt", headers=owner).status_code == 409
    assert client.put(f"/folders/{top['id']}/structure-exempt", headers=owner).status_code == 204
    assert _problems_of(_report(client), top["id"]) == []


def test_templates_are_admin_managed_and_validated(client, types):
    abteilung_id = types["abteilung"]["id"]
    team_id = types["team"]["id"]
    valid = {
        "name": "Abteilungsgerüst",
        "structure": {
            "name": "Abteilung",
            "object_type_id": abteilung_id,
            "children": [{"name": "Team 1", "object_type_id": team_id, "children": []}],
        },
    }
    assert client.post("/folder-templates", json=valid).status_code == 403

    invalid = {
        "name": "Falsch",
        "structure": {
            "name": "Abteilung",
            "object_type_id": abteilung_id,
            "children": [{"name": "Lose", "object_type_id": None, "children": []}],
        },
    }
    refused = client.post("/folder-templates", json=invalid, headers=ADMIN)
    assert refused.status_code == 422
    assert refused.json()["detail"]["errors"][0].startswith("/Abteilung/Lose:")

    created = client.post("/folder-templates", json=valid, headers=ADMIN)
    assert created.status_code == 201, created.text
    template_id = created.json()["id"]
    both = client.post(
        "/folder-templates",
        json={**valid, "source_folder_id": "root"},
        headers=ADMIN,
    )
    assert both.status_code == 422

    renamed = client.put(
        f"/folder-templates/{template_id}",
        json={"name": "Abteilung mit Team", "description": "Grundgerüst"},
        headers=ADMIN,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["structure"]["children"][0]["name"] == "Team 1"
    edited = client.put(
        f"/folder-templates/{template_id}",
        json={"structure": invalid["structure"]},
        headers=ADMIN,
    )
    assert edited.status_code == 422
    assert client.delete(f"/folder-templates/{template_id}").status_code == 403
    assert client.delete(f"/folder-templates/{template_id}", headers=ADMIN).status_code == 204


def test_folder_types_lookup(client, types):
    typed = _folder(client, name="Bauamt", object_type_id=types["abteilung"]["id"])
    untyped = _folder(client, name="Lose")
    response = client.post(
        "/folders/types", json={"ids": [typed["id"], untyped["id"], "does-not-exist"]}
    )
    assert response.json() == {
        "types": {typed["id"]: types["abteilung"]["id"], untyped["id"]: None}
    }
