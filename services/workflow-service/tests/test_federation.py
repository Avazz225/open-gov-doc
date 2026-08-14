"""Federation Hub (7.4, P6-S9). Läuft wie jeder andere Test dieses Service
gegen echte Infrastruktur (kein Mocking) - insbesondere den echten,
laufenden `federation-hub-service` (siehe conftest.py). Ein vollständiger
Ende-zu-Ende-Rundlauf (Hub liefert eine Zustellung tatsächlich an einen
horchenden Server aus) ist mit `TestClient` (In-Prozess-ASGI, kein echter
Netzwerk-Socket) nicht abbildbar - genau wie beim SLA-Poll-Loop (P6-S2)
bewusst dem Live-Docker-Smoke-Test überlassen (siehe
docs/services/workflow-service.md "Tests"). Diese Suite deckt stattdessen ab:
Krypto-Rundreise, echte Selbstregistrierung, Signatur-Ablehnung, und das
Dispatch-/Guard-Verhalten (inkl. eines echten, aber absichtlich
unerreichbaren Ziels für einen deterministischen "delivery_failed"-Fall)."""

import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from workflow_service import federation_crypto, repository
from workflow_service.main import app

# Eigenständig gelesen statt aus conftest importiert (siehe dortiger Kommentar:
# `from conftest import x` ist mit `--import-mode=importlib` über Testmodule
# hinweg nicht zuverlässig) - conftest.py setzt denselben Default bereits als
# Env-Var, bevor `workflow_service.main` importiert wird.
FEDERATION_HUB_SERVICE_URL = os.environ["DMS_FEDERATION_HUB_BASE_URL"]


def _federated_task_bpmn(*, target_installation_id: str, target_process_type: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
    id="Definitions_fed1"
    targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_federated" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="handover" />
    <bpmn:manualTask id="handover" name="An andere Installation übergeben">
      <bpmn:extensionElements>
        <camunda:properties>
          <camunda:property name="taskType" value="federated" />
          <camunda:property name="targetInstallationId" value="{target_installation_id}" />
          <camunda:property name="targetProcessType" value="{target_process_type}" />
        </camunda:properties>
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="handover" targetRef="Event_1" />
    <bpmn:endEvent id="Event_1">
      <bpmn:incoming>Flow_2</bpmn:incoming>
    </bpmn:endEvent>
  </bpmn:process>
</bpmn:definitions>
"""


def _federated_return_task_bpmn() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
    id="Definitions_fed2"
    targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_federated_return" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1">
      <bpmn:outgoing>Flow_1</bpmn:outgoing>
    </bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="return_result" />
    <bpmn:manualTask id="return_result" name="Ergebnis zurückschicken">
      <bpmn:extensionElements>
        <camunda:properties>
          <camunda:property name="taskType" value="federated_return" />
        </camunda:properties>
      </bpmn:extensionElements>
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="return_result" targetRef="Event_1" />
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
    """Registriert eine eigene, frei erfundene Installation direkt am echten
    Hub (kein Mocking) - Grundlage für Dispatch-Tests, die ein echtes,
    bekanntes Ziel im Adressbuch brauchen. Seit P13-S4 (ADR 0039) signiert
    statt mit einem vom Hub ausgegebenen API-Key - die Registrierung muss den
    Besitz des zum eingereichten ``public_key_pem`` passenden privaten
    Schlüssels nachweisen (Selbstkonsistenz-Prüfung, siehe
    `federation_hub_service.repository.register_or_update_installation`)."""
    private_pem, public_pem = federation_crypto.generate_keypair()
    payload = {
        "id": f"test-install-{uuid.uuid4().hex[:8]}",
        "display_name": "Test-Zielinstallation",
        "callback_base_url": "http://localhost:1",  # syntaktisch gültig, garantiert unerreichbar
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


def test_encrypt_decrypt_round_trip():
    private_pem, public_pem = federation_crypto.generate_keypair()
    payload = {"document_id": "doc-1", "notify_email": "reviewer@example.test"}

    encrypted = federation_crypto.encrypt_for(public_pem, payload)
    decrypted = federation_crypto.decrypt_with(private_pem, encrypted)

    assert decrypted == payload
    assert "document_id" not in encrypted  # kein Klartext im Envelope sichtbar


def test_decrypt_with_wrong_key_fails():
    private_pem_a, public_pem_a = federation_crypto.generate_keypair()
    private_pem_b, _ = federation_crypto.generate_keypair()
    encrypted = federation_crypto.encrypt_for(public_pem_a, {"secret": "value"})

    with pytest.raises(federation_crypto.DecryptionError):
        federation_crypto.decrypt_with(private_pem_b, encrypted)


def test_sign_and_verify_round_trip():
    private_pem, public_pem = federation_crypto.generate_keypair()
    body = b'{"hello":"world"}'

    signature = federation_crypto.sign_body(private_pem, body)

    assert federation_crypto.verify_body(public_pem, body, signature) is True


def test_verify_body_rejects_tampered_body():
    private_pem, public_pem = federation_crypto.generate_keypair()
    signature = federation_crypto.sign_body(private_pem, b"original")

    assert federation_crypto.verify_body(public_pem, b"tampered", signature) is False


async def test_workflow_service_registers_with_federation_hub(client):
    async with httpx.AsyncClient(base_url=FEDERATION_HUB_SERVICE_URL) as hub:
        installations = (await hub.get("/installations")).json()
    assert len(installations) >= 1

    response = client.get("/federation/installations")
    assert response.status_code == 200
    proxied = response.json()
    assert {i["id"] for i in installations} == {i["id"] for i in proxied}


def test_federation_inbound_requires_valid_signature(client):
    response = client.post(
        "/federation/inbound",
        content=b'{"handover_id":"x"}',
        headers={
            "Content-Type": "application/json",
            "X-Federation-Hub-Signature": "bm90LWEtcmVhbC1zaWduYXR1cmU=",
        },
    )
    assert response.status_code == 401


def test_federation_inbound_missing_signature_rejected(client):
    response = client.post("/federation/inbound", content=b'{"handover_id":"x"}')
    assert response.status_code == 401


def test_federation_inbound_respects_maintenance_mode(client):
    """4.8: föderierte Verarbeitung ist Alltagsverarbeitung, kein Admin-Vorgang
    - pausiert wie Instanzstart/Task-Abschluss während des Wartungsmodus."""
    response = client.post(
        "/federation/inbound",
        content=b'{"handover_id":"x"}',
        headers={"X-DMS-Maintenance-Active": "true"},
    )
    assert response.status_code == 503


def test_federation_inbound_result_respects_maintenance_mode(client):
    response = client.post(
        "/federation/inbound-result",
        content=b'{"handover_id":"x"}',
        headers={"X-DMS-Maintenance-Active": "true"},
    )
    assert response.status_code == 503


def test_federation_inbound_result_requires_valid_signature(client):
    response = client.post(
        "/federation/inbound-result",
        content=b'{"handover_id":"x"}',
        headers={
            "Content-Type": "application/json",
            "X-Federation-Hub-Signature": "bm90LWEtcmVhbC1zaWduYXR1cmU=",
        },
    )
    assert response.status_code == 401


async def test_complete_task_rejects_federated_task_directly(client, admin_headers):
    bpmn = _federated_task_bpmn(
        target_installation_id="does-not-exist", target_process_type="external-review"
    )
    create_response = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("federated.bpmn", bpmn, "application/xml")},
        data={"name": f"federated-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    )
    assert create_response.status_code == 201
    definition_id = create_response.json()["id"]

    instance_response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"}
    )
    assert instance_response.status_code == 201
    instance_id = instance_response.json()["id"]

    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["extensions"]["taskType"] == "federated"

    complete_response = client.post(
        f"/instances/{instance_id}/tasks/{tasks[0]['id']}/complete",
        json={"completed_by": "tester"},
    )
    assert complete_response.status_code == 409


async def test_dispatch_skips_unknown_target_and_leaves_task_ready(client, admin_headers):
    bpmn = _federated_task_bpmn(
        target_installation_id="does-not-exist", target_process_type="external-review"
    )
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("federated.bpmn", bpmn, "application/xml")},
        data={"name": f"federated-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"}
    ).json()["id"]

    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    assert len(tasks) == 1 and tasks[0]["extensions"]["taskType"] == "federated"


async def test_list_ready_tasks_excludes_federated_tasks(client, admin_headers):
    """`GET /tasks` (8, P14-S2 Reviewer/Approval-UI) darf einen `federated`-Task
    nicht als von einem Menschen abschließbare Aufgabe auflisten - er würde bei
    einem `.../complete`-Versuch ohnehin mit `409` abgelehnt (siehe
    `test_complete_task_rejects_federated_task_directly` oben)."""
    bpmn = _federated_task_bpmn(
        target_installation_id="does-not-exist", target_process_type="external-review"
    )
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("federated.bpmn", bpmn, "application/xml")},
        data={"name": f"federated-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"}
    ).json()["id"]

    all_tasks = client.get("/tasks").json()
    assert instance_id not in {t["instance_id"] for t in all_tasks}


async def test_dispatch_records_delivery_failed_for_unreachable_target(
    client, admin_headers, session
):
    """Seit Post-Roadmap Phase 20 Session 5 (ADR 0081) markiert der Hub einen
    EINZELNEN Zustellfehlschlag nicht mehr sofort als terminales
    `delivery_failed`, sondern als retry-fähiges `pending_retry` (erst nach
    Erschöpfung von `max_handover_delivery_attempts` wird es terminal) -
    `workflow-service`s `federation_task.status` übernimmt `handover["status"]`
    unverändert (`repository.update_federation_task_status`,
    `main.py`s `create_federation_task`-Aufrufer), daher hier ebenfalls
    `pending_retry` statt des ursprünglich (vor ADR 0081) erwarteten
    `delivery_failed`."""
    target_payload, _ = await _register_throwaway_installation()

    bpmn = _federated_task_bpmn(
        target_installation_id=target_payload["id"], target_process_type="external-review"
    )
    definition_id = client.post(
        "/process-definitions",
        files={"bpmn_xml": ("federated.bpmn", bpmn, "application/xml")},
        data={"name": f"federated-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"}
    ).json()["id"]

    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    task_id = tasks[0]["id"]

    federation_task = await repository.get_federation_task_by_task(session, instance_id, task_id)
    assert federation_task is not None
    assert federation_task.direction == "outbound"
    assert federation_task.status == "pending_retry"

    async with httpx.AsyncClient(base_url=FEDERATION_HUB_SERVICE_URL) as hub:
        handover = (await hub.get(f"/handovers/{federation_task.handover_id}")).json()
    assert handover["status"] == "pending_retry"
    assert handover["to_installation_id"] == target_payload["id"]


async def test_dispatch_federated_return_without_inbound_row_is_skipped(client, admin_headers):
    definition_id = client.post(
        "/process-definitions",
        files={
            "bpmn_xml": ("federated_return.bpmn", _federated_return_task_bpmn(), "application/xml")
        },
        data={"name": f"federated-return-{uuid.uuid4().hex[:8]}"},
        headers=admin_headers,
    ).json()["id"]

    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "tester"}
    ).json()["id"]

    tasks = client.get(f"/instances/{instance_id}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["extensions"]["taskType"] == "federated_return"


async def test_get_federation_task_by_handover_disambiguates_by_direction(
    session, manual_task_bpmn
):
    """Regressionstest für einen echten, erst beim Live-Smoke-Test gefundenen
    Bug: im Selbst-Loopback-Fall (eine Installation übergibt an sich selbst)
    landen zwei Zeilen mit demselben `handover_id` in derselben Datenbank
    (eine `outbound`, eine `inbound`) - `get_federation_task_by_handover` muss
    trotzdem eindeutig die `outbound`-Zeile liefern, sonst wirft
    `scalar_one_or_none()` `MultipleResultsFound` (siehe ADR 0028/PROGRESS.md).
    `process_instance_id` braucht echte Instanzen (Fremdschlüssel)."""
    definition = await repository.create_process_definition(
        session, name=f"fed-fk-{uuid.uuid4().hex[:8]}", bpmn_xml=manual_task_bpmn, process_id=None
    )
    instance_a = await repository.start_instance(
        session, definition.id, created_by="tester", business_key=None, initial_data={}
    )
    instance_b = await repository.start_instance(
        session, definition.id, created_by="tester", business_key=None, initial_data={}
    )
    await session.commit()

    handover_id = str(uuid.uuid4())
    await repository.create_federation_task(
        session,
        process_instance_id=instance_a.id,
        task_id="task-a",
        handover_id=handover_id,
        direction="outbound",
        origin_installation_id=None,
        status="delivered",
    )
    await repository.create_federation_task(
        session,
        process_instance_id=instance_b.id,
        task_id=None,
        handover_id=handover_id,
        direction="inbound",
        origin_installation_id="some-installation",
        status="received",
    )

    outbound = await repository.get_federation_task_by_handover(session, handover_id)
    assert outbound is not None
    assert outbound.direction == "outbound"
    assert outbound.process_instance_id == instance_a.id

    inbound = await repository.get_federation_task_by_handover(
        session, handover_id, direction="inbound"
    )
    assert inbound is not None
    assert inbound.direction == "inbound"
    assert inbound.process_instance_id == instance_b.id


# --- Versionskompatibilitäts-Konfiguration (7.4, P13-S3) --------------------


def test_get_federation_config_returns_a_value_without_prior_update(client):
    """Nicht auf einen bestimmten Wert geprüft (Settings-Default "1.0", aber
    ein früherer Testlauf gegen dieselbe DB kann ihn bereits per PUT geändert
    haben) - nur, dass der Seeding-Mechanismus überhaupt eine gültige
    Antwort liefert."""
    response = client.get("/federation/config")
    assert response.status_code == 200
    assert response.json()["version"]
    assert response.json()["min_compatible_peer_version"]


def test_update_federation_config_requires_admin(client):
    response = client.put(
        "/federation/config", json={"version": "2.0", "min_compatible_peer_version": "1.0"}
    )
    assert response.status_code == 403


def test_update_federation_config_round_trip(client, admin_headers):
    """7.4: "wird mit jedem Release explizit gepflegt, nicht implizit
    angenommen" - ein eindeutiger, aber gültig geformter Wert je Testlauf
    (rein numerisches (major, minor)-Schema, siehe `federation_hub_service.
    version_utils`) - ein PUT hier löst eine echte Re-Registrierung beim
    laufenden `federation-hub-service` aus (Singleton-Identität, geteilt mit
    jedem anderen Test dieses Moduls), ein ungültiges Format würde dort jede
    spätere `POST /handovers`-Vermittlung anderer Tests zum Absturz bringen
    (P13-S3-Fund, siehe ADR/PROGRESS.md). Stellt den vorherigen Stand am Ende
    wieder her, damit nachfolgende Tests denselben Ausgangszustand vorfinden."""
    previous = client.get("/federation/config").json()
    new_version = f"9.{uuid.uuid4().int % 900 + 100}"
    try:
        response = client.put(
            "/federation/config",
            json={"version": new_version, "min_compatible_peer_version": "1.0"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["version"] == new_version

        get_response = client.get("/federation/config")
        assert get_response.status_code == 200
        assert get_response.json()["version"] == new_version
    finally:
        client.put("/federation/config", json=previous, headers=admin_headers)


# --- Schlüsselrotation (7.4, P13-S4, ADR 0039) -------------------------------


def test_rotate_federation_key_requires_admin(client):
    response = client.post("/federation/rotate-key")
    assert response.status_code == 403


async def test_rotate_federation_key_updates_hub_registration(client, admin_headers):
    """Echter Rundlauf gegen den laufenden `federation-hub-service` (kein
    Mocking, wie der Rest dieser Suite) - bestätigt, dass eine Rotation
    tatsächlich ein neues Schlüsselpaar beim Hub hinterlegt, ohne andere
    Installationen zu beeinflussen."""
    before = {i["id"]: i["public_key_pem"] for i in client.get("/federation/installations").json()}

    response = client.post("/federation/rotate-key", headers=admin_headers)
    assert response.status_code == 200
    own_id = response.json()["installation_id"]
    assert own_id in before

    after = {i["id"]: i["public_key_pem"] for i in client.get("/federation/installations").json()}
    assert after[own_id] != before[own_id]
    for other_id in set(before) & set(after) - {own_id}:
        assert after[other_id] == before[other_id]
