import pytest
from fastapi.testclient import TestClient
from workflow_service.license_client import LicenseStatusClient
from workflow_service.main import app


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


def _set_license_status(monkeypatch, status: str) -> None:
    async def _fixed(self) -> str:
        return status

    monkeypatch.setattr(LicenseStatusClient, "get_status", _fixed)


def _upload_definition(client, xml: str, *, name: str, headers: dict[str, str]):
    files = {"bpmn_xml": ("process.bpmn", xml, "application/xml")}
    return client.post("/process-definitions", data={"name": name}, files=files, headers=headers)


def test_demo_mode_allows_read_but_blocks_write(
    client, monkeypatch, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    _set_license_status(monkeypatch, "demo")

    read_response = client.get(f"/process-definitions/{definition_id}")
    assert read_response.status_code == 200

    write_response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert write_response.status_code == 403


def test_unlicensed_blocks_read_and_write(client, monkeypatch, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    _set_license_status(monkeypatch, "unlicensed")

    read_response = client.get(f"/process-definitions/{definition_id}")
    assert read_response.status_code == 403

    write_response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert write_response.status_code == 403


def test_licensed_status_allows_read_and_write(client, monkeypatch, no_tasks_bpmn, admin_headers):
    _set_license_status(monkeypatch, "licensed")

    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasks", headers=admin_headers
    ).json()["id"]

    write_response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert write_response.status_code == 201
