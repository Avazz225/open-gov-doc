import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from workflow_service import main
from workflow_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Delegation scope resolution (4.4a, P32-S2, ADR 0130) - `document-service`/
# `object-type-service` round trips are real (no mocking); the
# `case-service` path is monkeypatched at `app.state.case_client.get_case`
# instead (see the object-type-scope tests below for why a real `POST
# /cases` call is structurally incompatible with this test suite).
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


def _create_delegation(
    *,
    deputy_principal_id: str,
    delegator_principal_id: str,
    process_definition_id: int | None = None,
    object_type_id: int | None = None,
    folder_resource_id: str | None = None,
) -> dict:
    """Stellvertretung bei Abwesenheit (4.4a, P14-S11) - echter Aufruf gegen
    den laufenden permission-service (kein Mocking, gleiches Prinzip wie
    `_grant_config_admin_permission` in conftest.py). `object_type_id`/
    `folder_resource_id` since P32-S2 (ADR 0130) - previously dead scope
    dimensions, now actually resolvable/enforceable at task completion."""
    now = datetime.now(UTC)
    body = {
        "deputy_principal_id": deputy_principal_id,
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
    }
    if process_definition_id is not None:
        body["scope_process_definition_ids"] = [process_definition_id]
    if object_type_id is not None:
        body["scope_object_type_ids"] = [object_type_id]
    if folder_resource_id is not None:
        body["scope_folder_resource_ids"] = [folder_resource_id]
    response = httpx.post(
        f"{PERMISSION_SERVICE_URL}/delegations",
        json=body,
        headers={"X-DMS-Principal": delegator_principal_id},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def _create_object_type(*, applies_to: str = "folder") -> int:
    """Delegation scope resolution (P32-S2) - `case-service`'s `POST /cases`
    validates a supplied `object_type_id` against a REAL object type via
    `object_type_client.validate` (no FK, but a live HTTP check), so a
    made-up integer would 400 - a minimal, unconstrained type is enough."""
    response = httpx.post(
        f"{OBJECT_TYPE_SERVICE_URL}/object-types",
        json={"name": f"wf-delegation-scope-test-{uuid.uuid4().hex[:8]}", "applies_to": applies_to},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["id"]


def _create_document(*, folder_id: str = "root") -> dict:
    """Delegation scope resolution (P32-S2) - fallback document-service
    path for `_resolve_business_key_scope` (no real process sets a document
    business_key today, see `document_client.py`'s own docstring, but the
    resolution path itself needs a real document to exercise). `folder_id`
    defaults to `"root"`, already a registered permission-service resource
    - no extra folder-service setup needed."""
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "wf-delegation-scope-test", "created_by": "alice", "folder_id": folder_id},
        files={"file": ("test.txt", b"delegation scope test content", "text/plain")},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def _create_supervisor_assignment(
    *, principal_id: str, supervisor_principal_id: str, users_admin_headers: dict[str, str]
) -> dict:
    """Org-hierarchy foundation (P31-S9) - echter Aufruf gegen den laufenden
    permission-service, gleiches Prinzip wie `_create_delegation`."""
    response = httpx.post(
        f"{PERMISSION_SERVICE_URL}/supervisor-assignments",
        json={"principal_id": principal_id, "supervisor_principal_id": supervisor_principal_id},
        headers=users_admin_headers,
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def _create_group_with_members(
    *, name: str, member_ids: list[str], users_admin_headers: dict[str, str]
) -> dict:
    group = httpx.post(
        f"{PERMISSION_SERVICE_URL}/groups",
        json={"name": name},
        headers=users_admin_headers,
        timeout=30.0,
    ).json()
    for principal_id in member_ids:
        httpx.post(
            f"{PERMISSION_SERVICE_URL}/groups/{group['id']}/members",
            json={"principal_id": principal_id},
            headers=users_admin_headers,
            timeout=30.0,
        ).raise_for_status()
    return group


def _upload_definition(
    client, xml: str, *, name: str, headers: dict[str, str], process_id: str | None = None
):
    data = {"name": name}
    if process_id is not None:
        data["process_id"] = process_id
    files = {"bpmn_xml": ("process.bpmn", xml, "application/xml")}
    return client.post("/process-definitions", data=data, files=files, headers=headers)


def _delete_definition(client, definition_id: int, headers: dict[str, str]):
    return client.delete(f"/process-definitions/{definition_id}", headers=headers)


def _upload_dmn(client, xml: str, *, name: str, headers: dict[str, str]):
    data = {"name": name}
    files = {"dmn_xml": ("decision.dmn", xml, "application/xml")}
    return client.post("/dmn-definitions", data=data, files=files, headers=headers)


def _delete_dmn(client, dmn_definition_id: int, headers: dict[str, str]):
    return client.delete(f"/dmn-definitions/{dmn_definition_id}", headers=headers)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "workflow-service"


def test_create_process_definition_without_permission_is_forbidden(client, manual_task_bpmn):
    response = _upload_definition(client, manual_task_bpmn, name="Approval", headers={})
    assert response.status_code == 403


def test_create_and_get_process_definition(client, manual_task_bpmn, admin_headers):
    create_response = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    )
    assert create_response.status_code == 201
    definition_id = create_response.json()["id"]
    assert create_response.json()["bpmn_process_id"] == "Process_cozt5fu"

    get_response = client.get(f"/process-definitions/{definition_id}")
    assert get_response.status_code == 200
    assert "bpmn:definitions" in get_response.json()["bpmn_xml"]


def test_create_process_definition_with_approval_required_defers_creation(
    client, manual_task_bpmn, admin_headers, users_admin_headers
):
    """Post-Roadmap Phase 21 Session 4 (ADR 0087) - mit aktivierter
    Genehmigungspflicht wird NICHT sofort angelegt, echte Integration gegen
    den lokal laufenden permission-service (kein Mocking), gleiches Muster
    wie config-service's `test_import_with_approval_required_defers_execution`.
    Der Erfolgsfall (Default, keine Genehmigungspflicht konfiguriert) bleibt
    unverändert `201` + `ProcessDefinitionOut` - siehe die zahlreichen
    anderen Tests in dieser Datei, die `_upload_definition(...).json()["id"]`
    unverändert weiterverwenden."""
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/workflow.process_definition.import",
        json={"requires_approval": True},
        headers=users_admin_headers,
    )
    try:
        name = f"Approval-Pending-{uuid.uuid4().hex[:8]}"
        response = _upload_definition(client, manual_task_bpmn, name=name, headers=admin_headers)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["result"] is None
        assert body["approval_request_id"] is not None

        # Keine sofortige Anlage - die Prozessfamilie existiert (noch) nicht.
        # Die tatsächliche Anwendung folgt asynchron über consumer.py, sobald
        # das Approval-Event eintrifft (siehe test_consumer.py).
        list_response = client.get("/process-definitions", params={"name": name})
        assert list_response.json() == []
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/workflow.process_definition.import",
            json={"requires_approval": False},
            headers=users_admin_headers,
        )


def test_create_process_definition_with_existing_name_creates_next_version(
    client, manual_task_bpmn, admin_headers
):
    first = _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    second = _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


def test_list_process_definitions_returns_only_latest_version_by_default(
    client, manual_task_bpmn, admin_headers
):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions")
    [approval] = [d for d in response.json() if d["name"] == "Approval"]
    assert approval["version"] == 2


def test_list_process_definitions_with_name_filter_returns_full_history(
    client, manual_task_bpmn, admin_headers
):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions", params={"name": "Approval"})
    assert [d["version"] for d in response.json()] == [2, 1]


def test_create_process_definition_invalid_bpmn_returns_422(client, admin_headers):
    response = client.post(
        "/process-definitions",
        data={"name": "Kaputt"},
        files={"bpmn_xml": ("process.bpmn", "not valid xml", "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_get_unknown_process_definition_returns_404(client):
    response = client.get("/process-definitions/999999")
    assert response.status_code == 404


def test_list_process_definitions(client, manual_task_bpmn, admin_headers):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions")
    assert response.status_code == 200
    assert any(d["name"] == "Approval" for d in response.json())


def test_delete_process_definition_without_permission_is_forbidden(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    response = _delete_definition(client, definition_id, headers={})

    assert response.status_code == 403


def test_delete_process_definition_with_instance_returns_409(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    client.post(f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"})
    response = _delete_definition(client, definition_id, headers=admin_headers)
    assert response.status_code == 409


def test_delete_process_definition_without_instances_succeeds(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    response = _delete_definition(client, definition_id, headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/process-definitions/{definition_id}").status_code == 404


def test_restore_process_definition_without_permission_is_forbidden(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    response = client.post(f"/process-definitions/{definition_id}/restore", headers={})

    assert response.status_code == 403


def test_restore_unknown_process_definition_returns_404(client, admin_headers):
    response = client.post("/process-definitions/999999/restore", headers=admin_headers)
    assert response.status_code == 404


def test_restore_process_definition_creates_next_version_with_matching_content(
    client, manual_task_bpmn, admin_headers
):
    """Rollback (P25-S2): restore aus einer älteren Version legt eine
    BRANDNEUE, nächste Version derselben Familie mit exakt deren Inhalt an -
    append-only, kein In-Place-Edit. Version 2 (ein zweiter regulärer Upload
    "dazwischen") bleibt unverändert bestehen, Version 3 (das Restore-
    Ergebnis) hat wieder exakt den `bpmn_xml`/`bpmn_process_id`-Inhalt von
    Version 1."""
    name = f"Restore-{uuid.uuid4().hex[:8]}"
    v1 = _upload_definition(client, manual_task_bpmn, name=name, headers=admin_headers).json()
    v1_detail = client.get(f"/process-definitions/{v1['id']}").json()

    # Eine zweite, inhaltlich andere Version "dazwischen" - stellt sicher,
    # dass restore tatsächlich den Inhalt der ZIELVERSION (v1) zurückbringt,
    # nicht einfach zufällig irgendeine ältere/neuere Version trifft.
    modified_bpmn = manual_task_bpmn.replace(
        'id="manual" name="manual"', 'id="manual" name="manual-geaendert"'
    )
    assert modified_bpmn != manual_task_bpmn
    v2 = _upload_definition(client, modified_bpmn, name=name, headers=admin_headers).json()
    assert v2["version"] == 2

    restore_response = client.post(
        f"/process-definitions/{v1['id']}/restore", headers=admin_headers
    )
    assert restore_response.status_code == 201
    v3 = restore_response.json()
    assert v3["version"] == 3
    assert v3["name"] == name
    assert v3["bpmn_process_id"] == v1_detail["bpmn_process_id"]
    assert v3["id"] != v1["id"]

    v3_detail = client.get(f"/process-definitions/{v3['id']}").json()
    assert v3_detail["bpmn_xml"] == v1_detail["bpmn_xml"]

    # Die Zwischenversion (v2) und die Ursprungsversion (v1) bleiben dabei
    # unverändert abrufbar - kein Überschreiben.
    history = client.get("/process-definitions", params={"name": name}).json()
    assert [d["version"] for d in history] == [3, 2, 1]


def test_restore_from_already_latest_version_creates_redundant_version(
    client, manual_task_bpmn, admin_headers
):
    """Bewusste Design-Entscheidung (P25-S2): ein Restore aus der bereits
    aktuellsten Version ist KEIN Sonderfall/No-Op - append-only wie jeder
    andere Aufruf, legt einfach eine weitere, inhaltlich identische Version
    an."""
    name = f"Restore-Latest-{uuid.uuid4().hex[:8]}"
    v1 = _upload_definition(client, manual_task_bpmn, name=name, headers=admin_headers).json()

    restore_response = client.post(
        f"/process-definitions/{v1['id']}/restore", headers=admin_headers
    )

    assert restore_response.status_code == 201
    assert restore_response.json()["version"] == 2


def test_create_dmn_definition_without_permission_is_forbidden(client, approval_level_dmn):
    response = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers={})
    assert response.status_code == 403


def test_create_and_get_dmn_definition(client, approval_level_dmn, admin_headers):
    create_response = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    )
    assert create_response.status_code == 201
    assert create_response.json()["decision_id"] == "approval-level"
    dmn_definition_id = create_response.json()["id"]

    get_response = client.get(f"/dmn-definitions/{dmn_definition_id}")
    assert get_response.status_code == 200
    assert "definitions" in get_response.json()["dmn_xml"]


def test_create_dmn_definition_with_existing_name_creates_next_version(
    client, approval_level_dmn, admin_headers
):
    first = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    second = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


def test_create_dmn_definition_invalid_dmn_returns_422(client, admin_headers):
    response = client.post(
        "/dmn-definitions",
        data={"name": "Kaputt"},
        files={"dmn_xml": ("decision.dmn", "not valid xml", "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_dmn_definition_duplicate_decision_id_returns_409(
    client, approval_level_dmn, admin_headers
):
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    response = _upload_dmn(client, approval_level_dmn, name="Andere Familie", headers=admin_headers)
    assert response.status_code == 409


def test_get_unknown_dmn_definition_returns_404(client):
    response = client.get("/dmn-definitions/999999")
    assert response.status_code == 404


def test_list_dmn_definitions_returns_only_latest_version_by_default(
    client, approval_level_dmn, admin_headers
):
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    response = client.get("/dmn-definitions")
    [freigabestufe] = [d for d in response.json() if d["name"] == "Freigabestufe"]
    assert freigabestufe["version"] == 2


def test_delete_dmn_definition_without_permission_is_forbidden(
    client, approval_level_dmn, admin_headers
):
    dmn_definition_id = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    ).json()["id"]
    response = _delete_dmn(client, dmn_definition_id, headers={})
    assert response.status_code == 403


def test_delete_dmn_definition_succeeds(client, approval_level_dmn, admin_headers):
    dmn_definition_id = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    ).json()["id"]
    response = _delete_dmn(client, dmn_definition_id, headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/dmn-definitions/{dmn_definition_id}").status_code == 404


def test_business_rule_task_process_definition_evaluates_dmn_end_to_end(
    client, business_rule_task_bpmn, approval_level_dmn, admin_headers
):
    """Ende-zu-Ende über die HTTP-API (P14-S4): DMN hochladen, referenzierende
    Prozessdefinition hochladen, Instanz starten - die Freigabestufe landet in
    den Task-Daten des abgeschlossenen Business Rule Task."""
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    definition_id = _upload_definition(
        client, business_rule_task_bpmn, name="Freigabe-Workflow", headers=admin_headers
    ).json()["id"]

    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"amount": 1500}},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_create_business_calendar_without_permission_is_forbidden(client):
    response = client.post(
        "/business-calendars", json={"name": "de-national", "non_working_dates": []}
    )
    assert response.status_code == 403


def test_create_and_get_business_calendar(client, admin_headers):
    create_response = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": ["2026-12-25"], "is_default": False},
        headers=admin_headers,
    )
    assert create_response.status_code == 201
    calendar_id = create_response.json()["id"]
    assert create_response.json()["non_working_dates"] == ["2026-12-25"]

    get_response = client.get(f"/business-calendars/{calendar_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "de-national"


def test_create_business_calendar_duplicate_name_returns_409(client, admin_headers):
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    response = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_create_business_calendar_invalid_date_returns_422(client, admin_headers):
    response = client.post(
        "/business-calendars",
        json={"name": "broken", "non_working_dates": ["not-a-date"]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_get_unknown_business_calendar_returns_404(client):
    response = client.get("/business-calendars/999999")
    assert response.status_code == 404


def test_list_business_calendars(client, admin_headers):
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    response = client.get("/business-calendars")
    assert response.status_code == 200
    assert any(c["name"] == "de-national" for c in response.json())


def test_update_business_calendar_without_permission_is_forbidden(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.put(
        f"/business-calendars/{calendar_id}",
        json={"name": "de-national", "non_working_dates": []},
    )
    assert response.status_code == 403


def test_update_business_calendar_changes_fields(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.put(
        f"/business-calendars/{calendar_id}",
        json={"name": "de-national", "non_working_dates": ["2026-12-25"], "is_default": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["non_working_dates"] == ["2026-12-25"]
    assert response.json()["is_default"] is True


def test_delete_business_calendar_succeeds(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.delete(f"/business-calendars/{calendar_id}", headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/business-calendars/{calendar_id}").status_code == 404


def test_business_days_timer_process_respects_calendar_end_to_end(
    client, business_days_timer_bpmn, admin_headers
):
    """Ende-zu-Ende über die HTTP-API (P14-S5): ein hochgeladener Kalender wirkt
    sich über `business_days()` tatsächlich auf eine gestartete Instanz aus -
    hier mit `n=0` deterministisch fast sofort abschließend."""
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    definition_id = _upload_definition(
        client, business_days_timer_bpmn, name="Wartezeit-Workflow", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201


def test_start_instance_with_manual_task_stays_running(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": "doc-1"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "running"
    assert response.json()["business_key"] == "doc-1"


def test_start_instance_fully_automatic_completes_immediately(client, no_tasks_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasks", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_start_instance_unknown_definition_returns_404(client):
    response = client.post("/process-definitions/999999/instances", json={"created_by": "alice"})
    assert response.status_code == 404


def test_start_instance_with_explicit_instance_id_uses_it(client, no_tasks_bpmn, admin_headers):
    """Caller-bestimmte Instanz-ID (P12-S2, gleiches Muster wie
    `federation-hub-service`s `handover_id`, ADR 0028) - wichtig für einen
    Aufrufer, der die ID bereits VOR dem Start persistieren will, um eine bei
    einem Fehlschlag trotzdem angelegte Instanz später wiederzufinden."""
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasksExplicitId", headers=admin_headers
    ).json()["id"]
    chosen_id = "caller-chosen-instance-id"
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "instance_id": chosen_id},
    )
    assert response.status_code == 201
    assert response.json()["id"] == chosen_id
    assert client.get(f"/instances/{chosen_id}").status_code == 200


def test_start_instance_rejected_during_maintenance_mode(client, manual_task_bpmn, admin_headers):
    """Retrofit P6-S6 (4.8): Instanzstart bleibt für jeden authentifizierten
    Principal offen, respektiert aber die Notfallsperre - der Header wird vom
    Gateway gesetzt, hier direkt simuliert (kein Gateway im Testlauf)."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )

    assert response.status_code == 503


def test_get_ready_tasks_and_complete_it(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()

    tasks_response = client.get(f"/instances/{instance['id']}/tasks")
    assert tasks_response.status_code == 200
    tasks = tasks_response.json()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "manual"

    complete_response = client.post(
        f"/instances/{instance['id']}/tasks/{tasks[0]['id']}/complete",
        json={"completed_by": "bob", "data": {"decision": "approved"}},
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    assert client.get(f"/instances/{instance['id']}/tasks").json() == []


def test_list_ready_tasks_spans_multiple_running_instances(client, manual_task_bpmn, admin_headers):
    """`GET /tasks` (8, P14-S2 Reviewer/Approval-UI) - bislang gab es nur die
    instanzgebundene `GET /instances/{id}/tasks`. Startet zwei Instanzen,
    schließt eine davon vollständig ab, und prüft, dass nur die noch offene
    Task der laufenden Instanz auftaucht, angereichert um `instance_id`."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="CrossInstanceTasks", headers=admin_headers
    ).json()["id"]
    running = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    to_complete = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task_to_complete = client.get(f"/instances/{to_complete['id']}/tasks").json()[0]
    client.post(
        f"/instances/{to_complete['id']}/tasks/{task_to_complete['id']}/complete",
        json={"completed_by": "bob"},
    )

    all_tasks = client.get("/tasks").json()
    matching = [t for t in all_tasks if t["instance_id"] == running["id"]]
    assert len(matching) == 1
    assert matching[0]["name"] == "manual"
    assert matching[0]["process_definition_id"] == definition_id
    assert matching[0]["business_key"] == running["business_key"]
    assert all(t["instance_id"] != to_complete["id"] for t in all_tasks)


def test_complete_task_rejected_during_maintenance_mode(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    tasks = client.get(f"/instances/{instance['id']}/tasks").json()

    response = client.post(
        f"/instances/{instance['id']}/tasks/{tasks[0]['id']}/complete",
        json={"completed_by": "bob"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )

    assert response.status_code == 503


def test_complete_unknown_task_returns_409(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()

    response = client.post(
        f"/instances/{instance['id']}/tasks/does-not-exist/complete",
        json={"completed_by": "bob"},
    )
    assert response.status_code == 409


# --- Task-Claim & dynamische Org-Hierarchie-Zugriffsfreigaben (14.2,
# Post-Roadmap Phase 31 Session 10) ------------------------------------------


def _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, *, name: str) -> dict:
    definition_id = _upload_definition(
        client, manual_task_bpmn, name=name, headers=admin_headers
    ).json()["id"]
    return client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "carla-creator"}
    ).json()


def test_claim_task_and_it_is_visible_in_task_listing(client, manual_task_bpmn, admin_headers):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim1")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee"},
    )
    assert response.status_code == 200
    assert response.json()["principal_id"] == "dora-assignee"

    tasks = client.get(f"/instances/{instance['id']}/tasks").json()
    assert tasks[0]["claimed_by"] == "dora-assignee"
    cross_instance = [t for t in client.get("/tasks").json() if t["instance_id"] == instance["id"]]
    assert cross_instance[0]["claimed_by"] == "dora-assignee"


def test_claim_task_by_same_principal_is_idempotent(client, manual_task_bpmn, admin_headers):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim2")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    first = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee"},
    ).json()
    second = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee"},
    ).json()
    assert first["id"] == second["id"]


def test_claim_task_already_claimed_by_different_principal_returns_409(
    client, manual_task_bpmn, admin_headers
):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim3")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee"},
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "someone-else"},
    )
    assert response.status_code == 409


def test_claim_unknown_task_returns_409(client, manual_task_bpmn, admin_headers):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim4")
    response = client.post(
        f"/instances/{instance['id']}/tasks/does-not-exist/claim",
        json={"principal_id": "dora-assignee"},
    )
    assert response.status_code == 409


def test_release_task_claim_clears_it(client, manual_task_bpmn, admin_headers):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim5")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee"},
    )

    response = client.delete(f"/instances/{instance['id']}/tasks/{task_id}/claim")
    assert response.status_code == 204

    tasks = client.get(f"/instances/{instance['id']}/tasks").json()
    assert tasks[0]["claimed_by"] is None


def test_release_unknown_task_claim_returns_404(client, manual_task_bpmn, admin_headers):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Claim6")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    response = client.delete(f"/instances/{instance['id']}/tasks/{task_id}/claim")
    assert response.status_code == 404


def test_org_hierarchy_grant_requires_existing_claim_returns_404(
    client, manual_task_bpmn, admin_headers
):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Grant1")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/org-hierarchy-grant",
        json={"grant_kind": "supervisor"},
    )
    assert response.status_code == 404


def test_org_hierarchy_grant_supervisor_grants_the_assignees_direct_supervisor(
    client, manual_task_bpmn, admin_headers, users_admin_headers
):
    _create_supervisor_assignment(
        principal_id="dora-assignee-g2",
        supervisor_principal_id="petra-supervisor-g2",
        users_admin_headers=users_admin_headers,
    )
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Grant2")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee-g2"},
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/org-hierarchy-grant",
        json={"grant_kind": "supervisor"},
    )
    assert response.status_code == 200
    assert response.json()["deputy_principal_ids"] == ["petra-supervisor-g2"]

    # Now a real, active delegation exists at permission-service.
    check = httpx.get(
        f"{PERMISSION_SERVICE_URL}/delegations/check",
        params={
            "deputy_principal_id": "petra-supervisor-g2",
            "delegator_principal_id": "dora-assignee-g2",
            "process_definition_id": instance["process_definition_id"],
        },
    ).json()
    assert check["allowed"] is True

    tasks = client.get(f"/instances/{instance['id']}/tasks").json()
    assert tasks[0]["grant_kind"] == "supervisor"


def test_org_hierarchy_grant_org_unit_without_org_unit_of_returns_422(
    client, manual_task_bpmn, admin_headers
):
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Grant3")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee-g3"},
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/org-hierarchy-grant",
        json={"grant_kind": "org_unit"},
    )
    assert response.status_code == 422


def test_org_hierarchy_grant_org_unit_of_creator_resolves_from_instance_creator(
    client, manual_task_bpmn, admin_headers, users_admin_headers
):
    """`created_by` beim Instanzstart ist hier immer "carla-creator" (siehe
    `_start_instance_with_one_task`) - die Gruppe muss also carla-creator
    enthalten, nicht die Assignee."""
    _create_group_with_members(
        name=f"OrgUnitWorkflowG4-{uuid.uuid4().hex[:8]}",
        member_ids=["carla-creator", "erik-colleague-g4"],
        users_admin_headers=users_admin_headers,
    )
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Grant4")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee-g4"},
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/org-hierarchy-grant",
        json={"grant_kind": "org_unit", "org_unit_of": "creator"},
    )
    assert response.status_code == 200
    assert response.json()["deputy_principal_ids"] == ["erik-colleague-g4"]


def test_completing_task_auto_releases_claim_and_revokes_grant(
    client, manual_task_bpmn, admin_headers, users_admin_headers
):
    _create_supervisor_assignment(
        principal_id="dora-assignee-g5",
        supervisor_principal_id="petra-supervisor-g5",
        users_admin_headers=users_admin_headers,
    )
    instance = _start_instance_with_one_task(client, manual_task_bpmn, admin_headers, name="Grant5")
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/claim",
        json={"principal_id": "dora-assignee-g5"},
    )
    client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/org-hierarchy-grant",
        json={"grant_kind": "supervisor"},
    )
    check_params = {
        "deputy_principal_id": "petra-supervisor-g5",
        "delegator_principal_id": "dora-assignee-g5",
        "process_definition_id": instance["process_definition_id"],
    }
    # Discriminating pre-check: the grant is genuinely active before
    # completion, so the post-completion assertion below actually proves
    # revocation happened, rather than the grant having never existed.
    assert (
        httpx.get(f"{PERMISSION_SERVICE_URL}/delegations/check", params=check_params).json()[
            "allowed"
        ]
        is True
    )

    complete_response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "dora-assignee-g5"},
    )
    assert complete_response.status_code == 200

    # "for the task's duration" - completion ends the grant immediately,
    # not only at its backstop `ends_at`.
    check = httpx.get(f"{PERMISSION_SERVICE_URL}/delegations/check", params=check_params).json()
    assert check["allowed"] is False


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


def test_complete_task_on_behalf_of_without_principal_header_returns_401(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob", "on_behalf_of_principal_id": "alice"},
        headers={"X-DMS-Principal": ""},
    )

    assert response.status_code == 401


def test_complete_task_on_behalf_of_without_active_delegation_returns_403(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob", "on_behalf_of_principal_id": "someone-without-delegation"},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_on_behalf_of_with_active_delegation_succeeds_and_annotates_event(
    client, manual_task_bpmn, admin_headers, monkeypatch
):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(deputy_principal_id=deputy, delegator_principal_id=delegator)

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 200

    completed_events = [e for e in published if e.event_type == "workflow.task.completed"]
    assert len(completed_events) == 1
    assert completed_events[0].actor == deputy
    assert completed_events[0].on_behalf_of == delegator


def test_complete_task_on_behalf_of_respects_process_definition_scope(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    other_definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval2", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    # Delegation gilt nur für einen ANDEREN Prozess als den, dessen Aufgabe
    # hier abgeschlossen werden soll - muss trotz existierender Delegation
    # abgelehnt werden.
    _create_delegation(
        deputy_principal_id=deputy,
        delegator_principal_id=delegator,
        process_definition_id=other_definition_id,
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_on_behalf_of_respects_object_type_scope(
    client, manual_task_bpmn, admin_headers, monkeypatch
):
    """P32-S2 (ADR 0130) - activates `scope_object_type_ids`, previously
    dead: `business_key` resolves via `case-service` to a real
    `object_type_id`. The case-service HTTP call itself is monkeypatched
    at `app.state.case_client.get_case` (same boundary-patch precedent as
    `test_complete_task_on_behalf_of_with_active_delegation_succeeds_and_
    annotates_event`'s event-bus patch above) - a real `POST /cases` call
    would itself trigger case-service's OWN `workflow_client.start_instance`
    against the live `workflow-service` container, which this test suite
    requires stopped (NATS durable-consumer isolation, `scripts/
    run-tests.sh`) - a genuine, unavoidable structural conflict, not a
    shortcut of convenience. `object_type_id` itself is still a real,
    live object-type-service row (`_create_object_type`) - only the case
    lookup is stubbed, not the object-type validation this ultimately
    depends on. A delegation scoped to a DIFFERENT object type must still
    be rejected, even though the process-definition scope (unset here)
    would otherwise allow it."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="ObjectTypeScope", headers=admin_headers
    ).json()["id"]
    matching_type_id = _create_object_type()
    other_type_id = _create_object_type()
    business_key = f"fake-case-{uuid.uuid4().hex[:8]}"

    async def fake_get_case(case_id: str, *, x_dms_principal: str) -> dict | None:
        if case_id == business_key:
            return {"id": case_id, "object_type_id": matching_type_id}
        return None

    monkeypatch.setattr(app.state.case_client, "get_case", fake_get_case)

    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": business_key},
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(
        deputy_principal_id=deputy, delegator_principal_id=delegator, object_type_id=other_type_id
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_on_behalf_of_allows_matching_object_type_scope(
    client, manual_task_bpmn, admin_headers, monkeypatch
):
    """Counterpart to the mismatch test above - a delegation scoped to the
    SAME object type as the resolved case must succeed, confirming the
    resolution path (not just the fail-closed rejection path) genuinely
    works end to end. Same `case_client.get_case` boundary patch as above,
    for the same structural reason."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="ObjectTypeScopeMatch", headers=admin_headers
    ).json()["id"]
    type_id = _create_object_type()
    business_key = f"fake-case-{uuid.uuid4().hex[:8]}"

    async def fake_get_case(case_id: str, *, x_dms_principal: str) -> dict | None:
        if case_id == business_key:
            return {"id": case_id, "object_type_id": type_id}
        return None

    monkeypatch.setattr(app.state.case_client, "get_case", fake_get_case)

    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": business_key},
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(
        deputy_principal_id=deputy, delegator_principal_id=delegator, object_type_id=type_id
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 200


def test_complete_task_on_behalf_of_respects_folder_resource_scope(
    client, manual_task_bpmn, admin_headers
):
    """P32-S2 (ADR 0130) - activates `scope_folder_resource_ids` via the
    document-service fallback path: no real process sets a document
    business_key today, but the resolution must still work correctly if
    one does. A delegation scoped to a folder OTHER than the resolved
    document's own folder must be rejected."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="FolderScope", headers=admin_headers
    ).json()["id"]
    document = _create_document(folder_id="root")
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": document["id"]},
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(
        deputy_principal_id=deputy,
        delegator_principal_id=delegator,
        folder_resource_id="not-the-documents-folder",
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_on_behalf_of_allows_matching_folder_resource_scope(
    client, manual_task_bpmn, admin_headers
):
    """Counterpart to the mismatch test above - a delegation scoped to
    `"root"` (the resolved document's own folder) must succeed."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="FolderScopeMatch", headers=admin_headers
    ).json()["id"]
    document = _create_document(folder_id="root")
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": document["id"]},
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(
        deputy_principal_id=deputy, delegator_principal_id=delegator, folder_resource_id="root"
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 200


def test_complete_task_without_on_behalf_of_needs_no_principal_header(
    client, manual_task_bpmn, admin_headers
):
    """Regulärer, nicht delegierter Abschluss bleibt unverändert möglich
    ohne X-DMS-Principal-Header - `completed_by` bleibt wie bisher ein
    ungeprüftes Freitextfeld, siehe main.py._require_delegation_if_on_behalf_of."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob"},
    )

    assert response.status_code == 200


def test_get_unknown_instance_returns_404(client):
    response = client.get("/instances/does-not-exist")
    assert response.status_code == 404


def test_get_ready_tasks_surfaces_signature_task_extensions(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()

    tasks = client.get(f"/instances/{instance['id']}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["extensions"] == {"taskType": "signature", "requiredLevel": "aes"}


def test_complete_signature_task_without_signature_id_returns_400(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete", json={"completed_by": "bob"}
    )
    assert response.status_code == 400


def test_complete_signature_task_with_unknown_signature_id_returns_400(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": "999999"},
    )
    assert response.status_code == 400


def test_complete_signature_task_with_mismatched_document_returns_400(
    client, signature_task_bpmn, admin_headers, real_signature
):
    document_id, signature_id, _level = real_signature
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "ein-anderes-dokument"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 400
    assert document_id != "ein-anderes-dokument"


def test_complete_signature_task_with_insufficient_level_returns_400(
    client, signature_task_bpmn, admin_headers, real_ses_signature
):
    document_id, signature_id, level = real_ses_signature
    assert level == "ses"
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": document_id}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 400


def test_complete_signature_task_with_valid_signature_succeeds(
    client, signature_task_bpmn, admin_headers, real_signature
):
    document_id, signature_id, level = real_signature
    assert level == "aes"
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": document_id}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_list_instances_filters_by_status(client, manual_task_bpmn, no_tasks_bpmn, admin_headers):
    running_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    completed_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasks", headers=admin_headers
    ).json()["id"]
    client.post(f"/process-definitions/{running_id}/instances", json={"created_by": "alice"})
    client.post(f"/process-definitions/{completed_id}/instances", json={"created_by": "alice"})

    running = client.get("/instances", params={"status": "running"}).json()
    completed = client.get("/instances", params={"status": "completed"}).json()
    assert len(running) == 1
    assert len(completed) == 1


def test_instance_with_connector_service_task_completes_via_stub(
    client, connector_service_task_bpmn, admin_headers, monkeypatch
):
    """Ende-zu-Ende (7.1, P12-S2): ein echter `POST /process-definitions/{id}/instances`
    treibt einen `connector_call`-Service-Task, der synchron gegen einen In-Prozess-
    HTTP-Stub aufgerufen wird (kein Mocking der eigenen Geschäftslogik, nur des
    ausgehenden Netzwerktransports - gleiches Prinzip wie `federation-hub-service`s
    Tests, dort mit `AsyncClient`/`ASGITransport`, hier synchron mit `MockTransport`)."""

    def stub(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://connector-stub.invalid/step"
        return httpx.Response(200, json={"result": "ok"})

    monkeypatch.setattr(
        main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
    )

    definition_id = _upload_definition(
        client, connector_service_task_bpmn, name="ConnectorCall", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_connector_service_task_service_url_supports_process_data_templating(
    client, connector_service_task_templated_bpmn, admin_headers, monkeypatch
):
    """`serviceUrl` kann `{platzhalter}` aus den aktuellen Prozessdaten referenzieren
    (P12-S2, Grundlage für migration-service's pro-Transfer unterschiedliche
    Schritt-Endpunkte) - hier `{transfer_id}`, gesetzt über `initial_data`."""
    called_urls = []

    def stub(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        return httpx.Response(200, json={"result": "ok"})

    monkeypatch.setattr(
        main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
    )

    definition_id = _upload_definition(
        client,
        connector_service_task_templated_bpmn,
        name="ConnectorCallTemplated",
        headers=admin_headers,
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"transfer_id": "abc-123"}},
    )
    assert response.status_code == 201
    assert called_urls == ["http://connector-stub.invalid/transfers/abc-123/steps/lock"]


def test_retry_instance_resumes_after_a_failed_connector_call(
    connector_service_task_bpmn, admin_headers, monkeypatch
):
    # Eigener `TestClient` mit `raise_server_exceptions=False` statt der geteilten
    # `client`-Fixture: Starlettes Default-Verhalten reicht eine unbehandelte
    # Exception zu Debug-Zwecken direkt an den Aufrufer durch, statt sie (wie ein
    # echter uvicorn-Prozess) als reguläre 500-Antwort zurückzugeben - genau diese
    # reale 500-Antwort will dieser Test aber tatsächlich sehen und weiterverarbeiten.
    with TestClient(
        app, raise_server_exceptions=False, headers={"X-DMS-Principal": "workflow-service-tests"}
    ) as client:
        attempts = {"count": 0}

        def stub(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError("Ziel nicht erreichbar", request=request)
            return httpx.Response(200, json={"result": "ok"})

        monkeypatch.setattr(
            main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
        )

        definition_id = _upload_definition(
            client, connector_service_task_bpmn, name="ConnectorCallRetry", headers=admin_headers
        ).json()["id"]
        start_response = client.post(
            f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
        )
        assert start_response.status_code == 500

        instance_id = client.get("/instances").json()[0]["id"]
        assert client.get(f"/instances/{instance_id}").json()["status"] == "running"

        retry_response = client.post(f"/instances/{instance_id}/retry")
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "completed"
    assert attempts["count"] == 2


def test_retry_instance_on_completed_instance_returns_409(client, no_tasks_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasksRetry", headers=admin_headers
    ).json()["id"]
    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()["id"]

    response = client.post(f"/instances/{instance_id}/retry")
    assert response.status_code == 409


def test_retry_unknown_instance_returns_404(client):
    response = client.post("/instances/does-not-exist/retry")
    assert response.status_code == 404
