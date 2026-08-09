import httpx
import pytest
from fastapi.testclient import TestClient
from workflow_service import main
from workflow_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


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
    with TestClient(app, raise_server_exceptions=False) as client:
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
