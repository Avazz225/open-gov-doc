import os
import uuid

import httpx
import pytest
from case_service import repository
from case_service.main import app
from dms_eventbus_client import Event
from fastapi.testclient import TestClient

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def process_definition_id(workflow_admin_headers: dict[str, str]) -> int:
    """Real gegen den lokal laufenden workflow-service angelegt (P6-S1) -
    gleiches "kein Mocking von Sibling-Services"-Muster wie document-services
    folder_client/object_type_client-Integrationstests. Seit P6-S6 verlangt
    dieser Endpunkt die Capability `admin.object_config`, siehe conftest.py."""
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
def no_tasks_process_definition_id(workflow_admin_headers: dict[str, str]) -> int:
    """A genuinely fully-automated process (zero tasks at all, start event
    straight to end event) - real BPMN registered against the real,
    running workflow-service, same pattern as `process_definition_id`
    above. Used for the Post-Roadmap Phase 44 Session 4 regression test
    proving a case whose process completes synchronously at start is
    actually closed, not left stuck `"open"`."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "no_tasks.bpmn")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{WORKFLOW_SERVICE_URL}/process-definitions",
            data={"name": f"case-service-test-no-tasks-{uuid.uuid4()}"},
            files={"bpmn_xml": ("process.bpmn", f, "application/xml")},
            headers=workflow_admin_headers,
        )
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture
def document_id(case_headers: dict[str, str]) -> str:
    """RBAC-Retrofit auf document-service's `POST /documents` (Post-Roadmap,
    Permission-Retrofit) - Aufruf braucht seither einen gültigen
    `X-DMS-Principal`, sonst 401. Gleicher Testprincipal wie `case_headers`
    (`case-service-tests`), der über die "everyone"-Rolle bereits
    `document.write` hält."""
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "Testdokument", "created_by": "alice"},
        files={"file": ("test.txt", b"Inhalt", "text/plain")},
        headers=case_headers,
    )
    response.raise_for_status()
    return response.json()["id"]


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "case-service"


def test_create_case_starts_workflow_instance(client, process_definition_id, case_headers):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["process_instance_id"] is not None
    assert body["closed_at"] is None


def test_create_case_for_a_fully_automated_process_closes_immediately(
    client, no_tasks_process_definition_id, case_headers
):
    """Post-Roadmap Phase 44 Session 4 - regression test for a real,
    previously-open race: a process with zero manual tasks completes
    SYNCHRONOUSLY inside `start_instance`, before this endpoint's own
    `Case` row is ever committed - `workflow.instance.completed` could
    therefore be published (and, since the same-process NATS consumer
    reacts near-instantly, plausibly processed) before the case exists,
    silently dropping the closure forever (see `consumer.py`'s early
    `return` when `get_case_or_none` finds nothing - ACKed, no retry).
    `create_case` now closes the case synchronously itself using the
    `instance["status"]` it already has in hand, sidestepping the race
    entirely rather than trying to fix event-delivery ordering."""
    response = client.post(
        "/cases",
        json={
            "name": "Vollautomatischer Vorgang",
            "process_definition_id": no_tasks_process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "closed"
    assert body["closed_at"] is not None
    assert body["process_instance_id"] is not None


@pytest.fixture
def object_type_with_close_requirement(workflow_admin_headers: dict[str, str]) -> int:
    """Real object type registered against the real, running
    object-type-service (Phase 45 Session 4, ADR 0165's successor) - a
    `statusTransitions` rule requiring `Abschlussgrund` before "open"->
    "closed" is allowed. `workflow_admin_headers` (`domain-admin-config`,
    `admin.object_config`) is reused unchanged - the same principal already
    creates process definitions in this same file."""
    response = httpx.post(
        f"{OBJECT_TYPE_SERVICE_URL}/object-types",
        json={
            "name": f"case-service-test-type-{uuid.uuid4().hex[:8]}",
            "applies_to": "document",
            "attributes": [{"name": "Abschlussgrund", "type": "string"}],
            "status_transitions": [
                {"from": "open", "to": "closed", "requiredAttributes": ["Abschlussgrund"]}
            ],
        },
        headers=workflow_admin_headers,
    )
    response.raise_for_status()
    return response.json()["id"]


def test_create_case_blocked_by_object_type_stays_open(
    client, no_tasks_process_definition_id, object_type_with_close_requirement, case_headers
):
    """Phase 45 Session 4 (ADR 0165's successor) - a fully-automated
    process still completes its BPMN instance, but the case itself stays
    `"open"` since the object type requires `Abschlussgrund` before
    closing, and this case was created without it."""
    response = client.post(
        "/cases",
        json={
            "name": "Blockierter Vorgang",
            "object_type_id": object_type_with_close_requirement,
            "process_definition_id": no_tasks_process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["closed_at"] is None
    assert body["process_instance_id"] is not None


def test_create_case_allowed_by_object_type_closes_immediately(
    client, no_tasks_process_definition_id, object_type_with_close_requirement, case_headers
):
    """Mirror of the blocked test above - supplying the required attribute
    at creation time (the only time `Case.attributes` can ever be set, see
    `status_transitions.close_with_validation`'s own docstring) lets the
    fully-automated closure go through as before this session."""
    response = client.post(
        "/cases",
        json={
            "name": "Erlaubter Vorgang",
            "object_type_id": object_type_with_close_requirement,
            "attributes": {"Abschlussgrund": "Erledigt"},
            "process_definition_id": no_tasks_process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "closed"
    assert body["closed_at"] is not None


def test_create_case_with_unknown_process_definition_returns_400(client, case_headers):
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_create_case_rejected_during_maintenance_mode(client):
    """Maintenance mode (4.8), Category A (Phase 56 Session 1, ADR 0152) -
    reads the gateway-forwarded `X-DMS-Maintenance-Active` header (no
    gateway in this test run, simulated directly), same pattern as
    `document-service`'s `test_create_document_rejected_during_maintenance_
    mode`. Fires before the `workflow_client.start_instance` cascade this
    check exists to guard - also before authentication, since it's checked
    first."""
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )
    assert response.status_code == 503


def test_create_case_requires_authentication(client):
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
    )
    assert response.status_code == 401


def test_create_case_returns_403_without_case_write_permission(client, everyone_role_without):
    everyone_role_without("case.write")
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
        headers={"X-DMS-Principal": "case-service-tests-no-write"},
    )
    assert response.status_code == 403


def test_get_and_list_cases(client, process_definition_id, case_headers):
    created = client.post(
        "/cases",
        json={
            "name": "Akte A",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    get_response = client.get(f"/cases/{created['id']}", headers=case_headers)
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Akte A"

    list_response = client.get("/cases", params={"status": "open"}, headers=case_headers)
    assert created["id"] in {c["id"] for c in list_response.json()}


def test_get_unknown_case_returns_404(client, case_headers):
    response = client.get("/cases/does-not-exist", headers=case_headers)
    assert response.status_code == 404


def test_list_cases_requires_authentication(client):
    response = client.get("/cases")
    assert response.status_code == 401


def test_list_cases_returns_403_without_case_read_permission(client, everyone_role_without):
    everyone_role_without("case.read")
    response = client.get("/cases", headers={"X-DMS-Principal": "case-service-tests-no-read"})
    assert response.status_code == 403


def test_list_cases_filters_out_a_case_isolated_from_the_default_grant(
    client, process_definition_id, case_headers
):
    """Row-level RBAC filtering (Post-Roadmap Phase 39 Session 4, ADR 0154)
    - closes the gap named alongside it: `GET /cases` previously checked
    only the collection-level `case.read` on `root`, then returned every
    row unfiltered, ignoring that ADR 0144 already lets an admin narrow an
    INDIVIDUAL case's own resource node (`inherit=False`, the same
    mechanism `teamspace-service`/`search-service`'s own tests use to
    anchor/prove per-resource isolation). `case_headers` keeps its default
    `case.read` via "everyone" throughout (no coarse collection-level gate
    is touched here) - only `isolated_case`'s OWN node stops inheriting
    that grant, so it must disappear from the list while `visible_case`
    (still inheriting normally) stays."""
    visible_case_id = client.post(
        "/cases",
        json={
            "name": f"Sichtbar-{uuid.uuid4().hex[:8]}",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]
    isolated_case_id = client.post(
        "/cases",
        json={
            "name": f"Isoliert-{uuid.uuid4().hex[:8]}",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]

    # The case's `ResourceNode` already exists (registered synchronously by
    # `create_case` itself, ADR 0144) - only its `inherit` flag needs
    # flipping, no `POST /resources` needed first.
    patch_response = httpx.patch(
        f"{PERMISSION_SERVICE_URL}/resources/{isolated_case_id}",
        json={"inherit": False},
        timeout=30.0,
    )
    patch_response.raise_for_status()

    response = client.get("/cases", headers=case_headers)
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert visible_case_id in ids
    assert isolated_case_id not in ids


def test_add_and_list_case_documents_resolves_current_version(
    client, process_definition_id, document_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    add_response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )
    assert add_response.status_code == 201
    assert add_response.json()["current_version_number"] == 1
    assert add_response.json()["document_deleted_at"] is None

    list_response = client.get(f"/cases/{case['id']}/documents", headers=case_headers)
    assert list_response.status_code == 200
    [reference] = list_response.json()
    assert reference["document_id"] == document_id
    assert reference["current_version_number"] == 1
    assert reference["snapshot_version_number"] is None


def test_case_documents_reflect_active_records_quarantine(
    client, process_definition_id, document_id, case_headers, records_quarantine_admin_headers
):
    """Records quarantine (14.2, ADR 0116, Post-Roadmap Phase 36 Session 2) -
    case-service surfaces the document's own quarantine status, set/released
    for real against the live-running document-service (no mocking)."""
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()
    client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )

    before = client.get(f"/cases/{case['id']}/documents", headers=case_headers).json()
    assert before[0]["has_active_quarantine"] is False

    quarantine = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/records-quarantine",
        json={"document_id": document_id, "set_by": "alice", "reason": "Prüfung"},
        headers=records_quarantine_admin_headers,
    )
    quarantine.raise_for_status()

    during = client.get(f"/cases/{case['id']}/documents", headers=case_headers).json()
    assert during[0]["has_active_quarantine"] is True

    release = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/records-quarantine/{quarantine.json()['id']}/release",
        json={"released_by": "bob"},
        headers=records_quarantine_admin_headers,
    )
    release.raise_for_status()

    after = client.get(f"/cases/{case['id']}/documents", headers=case_headers).json()
    assert after[0]["has_active_quarantine"] is False


def test_add_document_with_unknown_document_id_returns_400(
    client, process_definition_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": "does-not-exist", "added_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_add_document_to_unknown_case_returns_404(client, document_id, case_headers):
    response = client.post(
        "/cases/does-not-exist/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 404


def test_remove_document_reference_soft_deletes_and_stops_resolving_version(
    client, process_definition_id, document_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()
    client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )

    remove_response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/{document_id}",
        json={"removed_by": "bob"},
        headers=case_headers,
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["removed_by"] == "bob"

    [reference] = client.get(f"/cases/{case['id']}/documents", headers=case_headers).json()
    assert reference["removed_at"] is not None
    assert reference["current_version_number"] is None


def test_remove_unknown_reference_returns_404(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()
    response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/does-not-exist",
        json={"removed_by": "bob"},
        headers=case_headers,
    )
    assert response.status_code == 404


def test_list_cases_due_for_archival_empty(client):
    """Bewusst UNGEGATET (Post-Roadmap Phase 19 Session 5, ADR 0070) - reiner
    Maschine-zu-Maschine-Rückruf von `archival-service`, kein
    `X-DMS-Principal` nötig."""
    response = client.get("/cases/due-for-archival")
    assert response.status_code == 200
    assert response.json() == []


def test_create_case_assigns_unique_vorgangsnummer(client, process_definition_id, case_headers):
    first = client.post(
        "/cases",
        json={"name": "A", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()
    second = client.post(
        "/cases",
        json={"name": "B", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()

    assert first["vorgangsnummer"] is not None
    assert second["vorgangsnummer"] is not None
    assert first["vorgangsnummer"] != second["vorgangsnummer"]


# --- Draft / pre-registration lifecycle (post-roadmap phase 31 session 2,
# ADR 0113) -----------------------------------------------------------


def test_create_case_as_draft_has_no_vorgangsnummer_or_registered_at(
    client, process_definition_id, case_headers
):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["vorgangsnummer"] is None
    assert body["registered_at"] is None


def test_create_case_without_draft_is_registered_immediately(
    client, process_definition_id, case_headers
):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    assert response.json()["registered_at"] is not None


def test_register_draft_case_assigns_vorgangsnummer(client, process_definition_id, case_headers):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    ).json()["id"]

    response = client.post(
        f"/cases/{case_id}/register", json={"registered_by": "alice"}, headers=case_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["vorgangsnummer"] is not None
    assert body["registered_at"] is not None


def test_register_already_registered_case_returns_409(client, process_definition_id, case_headers):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]

    response = client.post(
        f"/cases/{case_id}/register", json={"registered_by": "alice"}, headers=case_headers
    )

    assert response.status_code == 409


def test_register_unknown_case_returns_404(client, case_headers):
    response = client.post(
        "/cases/does-not-exist/register", json={"registered_by": "alice"}, headers=case_headers
    )
    assert response.status_code == 404


def test_register_case_requires_case_write_permission(
    client, process_definition_id, case_headers, everyone_role_without
):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    ).json()["id"]

    everyone_role_without("case.write")
    response = client.post(
        f"/cases/{case_id}/register",
        json={"registered_by": "alice"},
        headers={"X-DMS-Principal": "case-service-tests-no-write"},
    )
    assert response.status_code == 403


def test_lookup_by_vorgangsnummer_finds_matching_case(client, process_definition_id, case_headers):
    created = client.post(
        "/cases",
        json={"name": "A", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()

    response = client.get(
        "/cases/by-vorgangsnummer",
        params={"value": created["vorgangsnummer"]},
        headers=case_headers,
    )

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [created["id"]]


def test_lookup_by_vorgangsnummer_returns_empty_list_when_unknown(client, case_headers):
    response = client.get(
        "/cases/by-vorgangsnummer", params={"value": "does-not-exist"}, headers=case_headers
    )
    assert response.status_code == 200
    assert response.json() == []


def test_case_number_config_get_put_roundtrip(client, case_headers):
    default = client.get("/case-number-config", headers=case_headers)
    assert default.status_code == 200
    assert default.json()["format"] == "{YYYY}-{Laufende_Nummer}"

    updated = client.put(
        "/case-number-config", json={"format": "{YY}/{Laufende_Nummer}"}, headers=case_headers
    )
    assert updated.status_code == 200
    assert updated.json()["format"] == "{YY}/{Laufende_Nummer}"


def test_case_number_config_rejects_format_without_laufende_nummer(client, case_headers):
    response = client.put("/case-number-config", json={"format": "{YYYY}"}, headers=case_headers)
    assert response.status_code == 400


def test_case_number_config_rejects_unknown_placeholder(client, case_headers):
    response = client.put(
        "/case-number-config",
        json={"format": "{Unbekannt}-{Laufende_Nummer}"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_case_archival_config_get_put_roundtrip(client, case_headers):
    default = client.get("/case-archival-config", headers=case_headers)
    assert default.status_code == 200
    assert default.json()["default_archive_after_days_closed"] is None
    assert default.json()["archive_encryption_enabled"] is False

    updated = client.put(
        "/case-archival-config",
        json={"default_archive_after_days_closed": 90, "archive_encryption_enabled": True},
        headers=case_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["default_archive_after_days_closed"] == 90
    assert updated.json()["archive_encryption_enabled"] is True


def test_request_archive_returns_409_for_open_case(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(f"/cases/{case['id']}/archive-request", headers=case_headers)

    assert response.status_code == 409


def test_archive_request_requires_authentication(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(f"/cases/{case['id']}/archive-request")

    assert response.status_code == 401


def test_get_case_archive_status_returns_404_for_unknown_case(client, case_headers):
    response = client.get("/cases/does-not-exist/archive-status", headers=case_headers)
    assert response.status_code == 404


async def test_archive_request_and_mark_archived_roundtrip_for_closed_case(
    client, session, case_headers
):
    case = await repository.create_case(
        session,
        case_id=f"case-{uuid.uuid4()}",
        name="Geschlossene Akte",
        object_type_id=None,
        attributes={},
        process_definition_id=1,
        process_instance_id=None,
        created_by="alice",
    )
    await repository.close_case(session, case, snapshots={})
    await session.commit()
    # Bypasses `POST /cases` (needs a real closed case without a full
    # workflow-completion round trip) - so its `ResourceNode`, normally
    # created there via the synchronous `POST /resources` call, must be
    # created here explicitly (Post-Roadmap Phase 35 Session 2, ADR 0144) -
    # otherwise the `resource_id=case_id` checks below would deny everyone
    # outright (an unregistered resource_id has no roles at all, see
    # `_require_case_permission`'s docstring). A plain `httpx` call, not
    # `app.state.permission_client` directly - that client was constructed
    # inside `TestClient`'s own internal lifespan/event loop, and awaiting
    # it from this test's own async context raises "bound to a different
    # event loop".
    async with httpx.AsyncClient() as pc:
        (
            await pc.post(
                f"{PERMISSION_SERVICE_URL}/resources",
                json={"resource_id": case.id, "parent_id": "root", "resource_type": "case"},
            )
        ).raise_for_status()

    request_response = client.post(f"/cases/{case.id}/archive-request", headers=case_headers)
    assert request_response.status_code == 200
    assert request_response.json()["archive_after"] is not None

    status_response = client.get(f"/cases/{case.id}/archive-status", headers=case_headers)
    assert status_response.status_code == 200
    assert status_response.json()["archived_at"] is None

    # Bewusst ohne Header - GET /cases/due-for-archival und
    # PUT /cases/{id}/archived sind ungegatete Maschine-zu-Maschine-Rückrufe
    # von archival-service (siehe main.py-Docstrings, ADR 0070).
    due = client.get("/cases/due-for-archival").json()
    assert case.id in [c["id"] for c in due]

    archived_response = client.put(f"/cases/{case.id}/archived")
    assert archived_response.status_code == 200
    assert archived_response.json()["archived_at"] is not None

    due_after = client.get("/cases/due-for-archival").json()
    assert case.id not in [c["id"] for c in due_after]


# --- Real per-case RBAC resource (Post-Roadmap Phase 35 Session 2, ADR 0144) --


def test_create_case_synchronously_registers_a_resource_node(
    client, process_definition_id, case_headers
):
    """The whole point of the synchronous `POST /resources` call (not just
    the fire-and-forget `case.resource.created` event) - the node must
    already exist the MOMENT `POST /cases` returns, no polling/waiting
    needed, unlike a purely event-driven registration would require."""
    created = client.post(
        "/cases",
        json={
            "name": "Fall mit echter Ressource",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    resource = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{created['id']}")
    assert resource.status_code == 200
    body = resource.json()
    assert body["parent_id"] == "root"
    assert body["resource_type"] == "case"


def test_create_case_also_publishes_resource_created_event(
    client, process_definition_id, case_headers, monkeypatch
):
    """For symmetry with `folder-service`'s own structure-event contract
    and as the basis for the startup backfill's self-healing - a harmless
    duplicate of the synchronous `POST /resources` call above, not the
    primary mechanism (see `create_case`'s own comment for why)."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.producer, "publish", fake_publish)

    created = client.post(
        "/cases",
        json={
            "name": "Fall mit Event",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    resource_events = [e for e in published if e.event_type == "case.resource.created"]
    assert len(resource_events) == 1
    assert resource_events[0].payload == {
        "resource_id": created["id"],
        "parent_id": "root",
        "resource_type": "case",
    }


def test_case_specific_role_assignment_restricts_access_to_that_case_only(
    client, process_definition_id, case_headers, everyone_role_without, role_admin_headers
):
    """The actual point of this session: a case's own `ResourceNode` lets a
    role be assigned scoped to just THAT case, not system-wide - proven by
    stripping `case.read` from "everyone" globally, then showing that only
    a principal with a case-specific grant can still read it, while every
    other case/principal is correctly denied."""
    case_id = client.post(
        "/cases",
        json={
            "name": "Eingeschränkter Fall",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]
    other_case_id = client.post(
        "/cases",
        json={
            "name": "Anderer Fall",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]

    everyone_role_without("case.read")

    # Ohne "everyone"-Grant ist jetzt JEDER Fall für JEDEN unlesbar - auch
    # für case_headers selbst, das keine spezifische Rolle hat.
    assert client.get(f"/cases/{case_id}", headers=case_headers).status_code == 403
    assert client.get(f"/cases/{other_case_id}", headers=case_headers).status_code == 403

    scoped_principal = "case-service-tests-scoped-reader"
    created_role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"case-reader-{case_id}", "permissions": ["case.read"]},
        headers=role_admin_headers,
    ).json()
    # Wrapped `RoleActionResult` (ADR 0130) - {status, role, approval_request_id}.
    assert created_role["status"] == "created"
    role_id = created_role["role"]["id"]
    assignment_response = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": scoped_principal,
            "role_id": role_id,
            "resource_id": case_id,
        },
    )
    assignment_response.raise_for_status()
    # Same wrapped shape (P17-S3) - `raise_for_status()` alone wouldn't
    # catch a silently pending-approval assignment (always 2xx either way).
    assert assignment_response.json()["status"] == "created"

    # Der gezielt berechtigte Principal darf jetzt GENAU diesen einen Fall
    # lesen - resource_id=case_id wird also wirklich ausgewertet, nicht
    # weiterhin pauschal "root".
    scoped_headers = {"X-DMS-Principal": scoped_principal}
    assert client.get(f"/cases/{case_id}", headers=scoped_headers).status_code == 200
    # ...aber NICHT den anderen Fall - die Berechtigung ist wirklich auf
    # genau diese eine Ressource begrenzt, keine versehentliche
    # Root-Freigabe.
    assert client.get(f"/cases/{other_case_id}", headers=scoped_headers).status_code == 403


def test_collection_level_case_endpoints_still_check_root(
    client, process_definition_id, case_headers, everyone_role_without
):
    """Regression guard: `POST`/`GET /cases` have no single case to check
    against yet - they must keep checking `root`, unaffected by this
    session's per-case switch for the other endpoints."""
    everyone_role_without("case.read")

    response = client.get("/cases", headers=case_headers)

    assert response.status_code == 403
