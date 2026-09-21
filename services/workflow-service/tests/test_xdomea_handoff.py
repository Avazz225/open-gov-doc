"""DMS-to-DMS XDOMEA handoff (7.4/14.2, Post-Roadmap Phase 43 Session 1,
ADR 0147/ADR 0159). The outbound dispatch guards/happy path run against the
real, running `federation-hub-service` (same "no mocking of the hub"
convention as `test_federation.py`, whose helpers this file duplicates
rather than importing - see that file's own comment on why cross-module
`conftest` imports aren't reliable here with `--import-mode=importlib`).
The receiving side's own import/confirmation LOGIC (`_handle_inbound_
xdomea_handoff`) is instead called directly with a boundary-patched
`archival_client`/`federation_client`, the same "boundary-patch the
client" convention `test_api.py` already uses for `case_client.get_case` -
a full round trip through a real HTTP callback needs a real listening
socket `TestClient` cannot provide (see `test_federation.py`'s own
docstring), so that stays reserved for the live Docker smoke test."""

import base64
import json
import os
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from workflow_service import federation_crypto, repository
from workflow_service.archival_client import ArchivalServiceClient
from workflow_service.federation_client import FederationHubClient
from workflow_service.main import _handle_inbound_xdomea_handoff, app, settings
from workflow_service.models import FederationIdentity

FEDERATION_HUB_SERVICE_URL = os.environ["DMS_FEDERATION_HUB_BASE_URL"]


def _xdomea_handoff_bpmn(*, target_installation_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
    id="Definitions_xdomea1"
    targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_xdomea_handoff" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="handoff" />
    <bpmn:manualTask id="handoff" name="Fall an andere Installation übergeben">
      <bpmn:extensionElements>
        <camunda:properties>
          <camunda:property name="taskType" value="federated" />
          <camunda:property name="targetInstallationId" value="{target_installation_id}" />
          <camunda:property name="targetProcessType" value="xdomea.case_handoff" />
        </camunda:properties>
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="handoff" targetRef="Event_1" />
    <bpmn:endEvent id="Event_1">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
  </bpmn:process>
</bpmn:definitions>
"""


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


async def _register_throwaway_installation(**overrides) -> tuple[dict, bytes]:
    private_pem, public_pem = federation_crypto.generate_keypair()
    payload = {
        "id": f"test-install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Zielinstallation",
        # Phase 60 Session 1 (ADR 0183): see test_federation.py's identical
        # helper for why this is no longer a literal loopback address.
        "callback_base_url": "http://unreachable.invalid:1",
        "public_key_pem": public_pem.decode("utf-8"),
        "version": "1.0",
        "min_compatible_peer_version": "1.0",
    }
    payload.update(overrides)
    body = json.dumps(payload).encode("utf-8")
    async with httpx.AsyncClient(base_url=FEDERATION_HUB_SERVICE_URL) as hub:
        response = await hub.post(
            "/installations",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Installation-Signature": federation_crypto.sign_body(private_pem, body),
            },
        )
        response.raise_for_status()
    return payload, private_pem


# --- Outbound dispatch (sending side) ---------------------------------


async def test_dispatch_builds_xdomea_package_instead_of_raw_task_data(
    client, admin_headers, monkeypatch
):
    target_payload, _ = await _register_throwaway_installation()

    exported = {}

    async def fake_export_case(self, case_id, *, leser_name):
        exported["case_id"] = case_id
        exported["leser_name"] = leser_name
        return b"fake-zip-bytes"

    monkeypatch.setattr(ArchivalServiceClient, "export_case", fake_export_case)

    sent_payload = {}
    real_encrypt_for = federation_crypto.encrypt_for

    def spying_encrypt_for(public_key_pem, payload):
        sent_payload.update(payload)
        return real_encrypt_for(public_key_pem, payload)

    monkeypatch.setattr(federation_crypto, "encrypt_for", spying_encrypt_for)

    bpmn = _xdomea_handoff_bpmn(target_installation_id=target_payload["id"])
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("xdomea.bpmn", bpmn, "application/xml")},
        data={"name": f"xdomea-handoff-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "tester", "initial_data": {"case_id": "case-123"}},
    ).json()["id"]

    assert exported == {"case_id": "case-123", "leser_name": "Test-Zielinstallation"}
    assert sent_payload["case_id"] == "case-123"
    assert sent_payload["package_base64"] == base64.b64encode(b"fake-zip-bytes").decode("ascii")

    # Same "never completed here, only via a later inbound-result" contract
    # as the generic case - dispatch alone does not complete the task.
    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["extensions"]["taskType"] == "federated"


async def test_dispatch_skips_xdomea_handoff_missing_case_id(client, admin_headers, monkeypatch):
    target_payload, _ = await _register_throwaway_installation()

    called = False

    async def fake_export_case(self, case_id, *, leser_name):
        nonlocal called
        called = True
        return b"unused"

    monkeypatch.setattr(ArchivalServiceClient, "export_case", fake_export_case)

    bpmn = _xdomea_handoff_bpmn(target_installation_id=target_payload["id"])
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("xdomea.bpmn", bpmn, "application/xml")},
        data={"name": f"xdomea-handoff-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    # No `initial_data` at all - `task.data.get("case_id")` is missing.
    client.post(f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"})

    assert called is False


async def test_dispatch_skips_xdomea_handoff_on_export_failure(
    client, admin_headers, monkeypatch, session
):
    target_payload, _ = await _register_throwaway_installation()

    async def failing_export_case(self, case_id, *, leser_name):
        raise httpx.HTTPStatusError(
            "boom", request=httpx.Request("POST", "http://x"), response=httpx.Response(404)
        )

    monkeypatch.setattr(ArchivalServiceClient, "export_case", failing_export_case)

    bpmn = _xdomea_handoff_bpmn(target_installation_id=target_payload["id"])
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("xdomea.bpmn", bpmn, "application/xml")},
        data={"name": f"xdomea-handoff-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "tester", "initial_data": {"case_id": "case-123"}},
    ).json()["id"]

    # No crash, task stays ready, no FederationTask row was created (export
    # failed before the handover was ever created).
    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    assert len(tasks) == 1
    task_id = tasks[0]["id"]
    federation_task = await repository.get_federation_task_by_task(session, instance_id, task_id)
    assert federation_task is None


# --- Inbound handling (receiving side) ---------------------------------


def _fake_identity() -> FederationIdentity:
    private_pem, public_pem = federation_crypto.generate_keypair()
    return FederationIdentity(
        id=999999,
        installation_id="our-test-installation",
        private_key_pem=private_pem,
        public_key_pem=public_pem,
        hub_public_key_pem=b"unused-in-this-test",
        created_at=datetime.now(UTC),
    )


def _fake_origin_installation() -> tuple[dict, bytes]:
    private_pem, public_pem = federation_crypto.generate_keypair()
    return {
        "id": "origin-test-installation",
        "public_key_pem": public_pem.decode("utf-8"),
    }, private_pem


def _inbound_payload(*, identity: FederationIdentity, origin: dict, package_base64: str) -> dict:
    encrypted_payload = federation_crypto.encrypt_for(
        identity.public_key_pem, {"package_base64": package_base64, "case_id": "case-123"}
    )
    return {
        "handover_id": str(uuid.uuid4()),
        "from_installation_id": origin["id"],
        "process_type": "xdomea.case_handoff",
        "encrypted_payload": encrypted_payload,
    }


async def test_handle_inbound_xdomea_handoff_imports_and_sends_confirmation(
    client, session, monkeypatch
):
    monkeypatch.setattr(settings, "xdomea_handoff_target_folder_id", "folder-1")
    monkeypatch.setattr(settings, "xdomea_handoff_process_definition_id", 42)

    identity = _fake_identity()
    origin, origin_private = _fake_origin_installation()
    package_bytes = b"real-zip-bytes"
    payload = _inbound_payload(
        identity=identity,
        origin=origin,
        package_base64=base64.b64encode(package_bytes).decode("ascii"),
    )

    async def fake_list_installations(self):
        return [origin]

    monkeypatch.setattr(FederationHubClient, "list_installations", fake_list_installations)

    sent = {}

    async def fake_send_result(self, **kwargs):
        sent.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(FederationHubClient, "send_result", fake_send_result)

    imported = {}

    async def fake_import_package(self, zip_bytes, *, folder_id, process_definition_id):
        imported["zip_bytes"] = zip_bytes
        imported["folder_id"] = folder_id
        imported["process_definition_id"] = process_definition_id
        return {
            "case_id": "new-case-1",
            "case_created": True,
            "vorgang_betreff": "Testvorgang",
            "document_ids": ["doc-1", "doc-2"],
            "skipped_document_count": 0,
            "skipped_schriftstueck_count": 0,
        }

    monkeypatch.setattr(ArchivalServiceClient, "import_package", fake_import_package)

    result = await _handle_inbound_xdomea_handoff(session, payload, identity)

    assert imported == {
        "zip_bytes": package_bytes,
        "folder_id": "folder-1",
        "process_definition_id": 42,
    }
    assert result.status == "imported"
    assert result.case_id == "new-case-1"
    assert result.case_created is True
    assert result.document_ids == ["doc-1", "doc-2"]

    assert sent["handover_id"] == payload["handover_id"]
    assert sent["outcome"] == "completed"
    decrypted_result = federation_crypto.decrypt_with(origin_private, sent["encrypted_result"])
    assert decrypted_result["status"] == "imported"
    assert decrypted_result["case_id"] == "new-case-1"

    federation_task = await repository.get_federation_task_by_handover(
        session, payload["handover_id"], direction="inbound"
    )
    assert federation_task is not None
    assert federation_task.process_instance_id is None
    assert federation_task.origin_installation_id == origin["id"]


async def test_handle_inbound_xdomea_handoff_rejects_when_not_configured(
    client, session, monkeypatch
):
    monkeypatch.setattr(settings, "xdomea_handoff_target_folder_id", None)
    monkeypatch.setattr(settings, "xdomea_handoff_process_definition_id", None)

    identity = _fake_identity()
    origin, _ = _fake_origin_installation()
    payload = _inbound_payload(
        identity=identity, origin=origin, package_base64=base64.b64encode(b"x").decode("ascii")
    )

    with pytest.raises(HTTPException) as exc_info:
        await _handle_inbound_xdomea_handoff(session, payload, identity)
    assert exc_info.value.status_code == 422


async def test_handle_inbound_xdomea_handoff_rejects_missing_package_base64(
    client, session, monkeypatch
):
    monkeypatch.setattr(settings, "xdomea_handoff_target_folder_id", "folder-1")
    monkeypatch.setattr(settings, "xdomea_handoff_process_definition_id", 42)

    identity = _fake_identity()
    origin, _ = _fake_origin_installation()
    encrypted_payload = federation_crypto.encrypt_for(identity.public_key_pem, {"case_id": "x"})
    payload = {
        "handover_id": str(uuid.uuid4()),
        "from_installation_id": origin["id"],
        "process_type": "xdomea.case_handoff",
        "encrypted_payload": encrypted_payload,
    }

    with pytest.raises(HTTPException) as exc_info:
        await _handle_inbound_xdomea_handoff(session, payload, identity)
    assert exc_info.value.status_code == 422


async def test_handle_inbound_xdomea_handoff_sends_failure_confirmation_on_import_error(
    client, session, monkeypatch
):
    monkeypatch.setattr(settings, "xdomea_handoff_target_folder_id", "folder-1")
    monkeypatch.setattr(settings, "xdomea_handoff_process_definition_id", 42)

    identity = _fake_identity()
    origin, origin_private = _fake_origin_installation()
    payload = _inbound_payload(
        identity=identity, origin=origin, package_base64=base64.b64encode(b"x").decode("ascii")
    )

    async def fake_list_installations(self):
        return [origin]

    monkeypatch.setattr(FederationHubClient, "list_installations", fake_list_installations)

    sent = {}

    async def fake_send_result(self, **kwargs):
        sent.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(FederationHubClient, "send_result", fake_send_result)

    async def failing_import_package(self, zip_bytes, *, folder_id, process_definition_id):
        raise httpx.HTTPStatusError(
            "invalid package",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(422),
        )

    monkeypatch.setattr(ArchivalServiceClient, "import_package", failing_import_package)

    with pytest.raises(HTTPException) as exc_info:
        await _handle_inbound_xdomea_handoff(session, payload, identity)
    assert exc_info.value.status_code == 502

    # The origin installation is still notified, with a failure outcome -
    # its own pending `federated` task must not be left hanging forever
    # just because the import itself failed.
    assert sent["outcome"] == "failed"
    decrypted_result = federation_crypto.decrypt_with(origin_private, sent["encrypted_result"])
    assert decrypted_result["status"] == "failed"
