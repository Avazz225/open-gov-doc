"""A workflow start without a known outcome (P95-S1c): a timeout, connection
error or `5xx` from workflow-service no longer leaves an unlinked case behind
with a `500`. The instance is looked up and adopted if it exists; otherwise
the caller gets `503` with the case id, and the reconciliation settles the
case after the grace period (adopt or remove; keep if documents were added).
Real workflow-service; the failure is injected at the client."""

import os
import uuid

import httpx
import pytest
from case_service import main as case_main
from case_service import repository
from case_service.main import app
from case_service.workflow_client import WorkflowUnavailableError
from fastapi.testclient import TestClient

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def process_definition_id(workflow_admin_headers: dict[str, str]) -> int:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{WORKFLOW_SERVICE_URL}/process-definitions",
            data={"name": f"case-service-test-{uuid.uuid4()}"},
            files={"bpmn_xml": ("process.bpmn", f, "application/xml")},
            headers=workflow_admin_headers,
        )
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture
def start_times_out(monkeypatch):
    """`start_instance` raises as a timeout would. With `really_start`, the
    instance is created first - the start went through, only the answer was
    lost."""
    workflow_client = app.state.workflow_client
    original = workflow_client.start_instance

    def arrange(*, really_start: bool) -> None:
        async def timed_out(*args, **kwargs):
            if really_start:
                await original(*args, **kwargs)
            raise WorkflowUnavailableError("ReadTimeout: simulated")

        monkeypatch.setattr(workflow_client, "start_instance", timed_out)

    return arrange


def _create(client, case_headers, definition_id: int, name: str):
    return client.post(
        "/cases",
        json={"name": name, "process_definition_id": definition_id},
        headers=case_headers,
    )


def _reconcile(client, monkeypatch) -> dict[str, int]:
    monkeypatch.setattr(case_main.settings, "unstarted_case_grace_seconds", 0.0)
    return client.portal.call(case_main._reconcile_unstarted_cases)


def test_a_start_that_went_through_is_adopted_at_once(
    client, case_headers, process_definition_id, start_times_out
):
    start_times_out(really_start=True)
    response = _create(client, case_headers, process_definition_id, "Angenommen")
    assert response.status_code == 201, response.text
    case = response.json()
    assert case["process_instance_id"]
    instance = httpx.get(f"{WORKFLOW_SERVICE_URL}/instances/{case['process_instance_id']}").json()
    assert instance["business_key"] == case["id"]
    # The rest of the creation happened too: the case is a permission resource.
    assert client.get(f"/cases/{case['id']}", headers=case_headers).status_code == 200


def test_an_unknown_outcome_answers_503_and_the_reconciliation_removes_the_case(
    client, case_headers, process_definition_id, start_times_out, monkeypatch
):
    start_times_out(really_start=False)
    response = _create(client, case_headers, process_definition_id, "Nie gestartet")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    case_id = response.json()["detail"]["case_id"]

    # Within the grace period nothing is touched - the start may still run.
    assert client.portal.call(case_main._reconcile_unstarted_cases) == {
        "adopted": 0,
        "removed": 0,
        "kept": 0,
    }
    assert _reconcile(client, monkeypatch) == {"adopted": 0, "removed": 1, "kept": 0}
    assert all(c["id"] != case_id for c in client.get("/cases", headers=case_headers).json())


def test_the_reconciliation_adopts_an_instance_that_appeared_late(
    client, case_headers, process_definition_id, start_times_out, monkeypatch
):
    start_times_out(really_start=True)
    # The lookup right after the timeout doesn't see it yet.
    workflow_client = app.state.workflow_client
    original_find = workflow_client.find_instance

    async def not_yet(_business_key):
        return None

    monkeypatch.setattr(workflow_client, "find_instance", not_yet)
    response = _create(client, case_headers, process_definition_id, "Spät gefunden")
    assert response.status_code == 503
    case_id = response.json()["detail"]["case_id"]

    monkeypatch.setattr(workflow_client, "find_instance", original_find)
    assert _reconcile(client, monkeypatch) == {"adopted": 1, "removed": 0, "kept": 0}
    case = client.get(f"/cases/{case_id}", headers=case_headers).json()
    instance = httpx.get(f"{WORKFLOW_SERVICE_URL}/instances/{case['process_instance_id']}").json()
    assert instance["business_key"] == case_id


def test_a_case_with_documents_is_kept(
    client, case_headers, process_definition_id, start_times_out, monkeypatch
):
    start_times_out(really_start=False)
    response = _create(client, case_headers, process_definition_id, "Mit Dokument")
    case_id = response.json()["detail"]["case_id"]

    async def add_reference():
        async with app.state.session_factory() as session:
            await repository.add_document_reference(
                session, case_id, document_id=str(uuid.uuid4()), added_by="alice"
            )
            await session.commit()

    client.portal.call(add_reference)
    assert _reconcile(client, monkeypatch) == {"adopted": 0, "removed": 0, "kept": 1}

    async def still_there():
        async with app.state.session_factory() as session:
            return await repository.get_case_or_none(session, case_id)

    assert client.portal.call(still_there) is not None


def test_the_reconciliation_pauses_while_workflow_service_is_down(
    client, case_headers, process_definition_id, start_times_out, monkeypatch
):
    start_times_out(really_start=False)
    _create(client, case_headers, process_definition_id, "Später")

    async def down(_business_key):
        raise WorkflowUnavailableError("ConnectError: simulated")

    monkeypatch.setattr(app.state.workflow_client, "find_instance", down)
    assert _reconcile(client, monkeypatch) == {"adopted": 0, "removed": 0, "kept": 0}
