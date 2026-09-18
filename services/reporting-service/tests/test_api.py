import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from reporting_service import repository
from reporting_service.main import _run_due_schedules, app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
REPORTING_TEST_PRINCIPAL_ID = "reporting-service-tests"
# Post-Roadmap Phase 19 Session 6 (ADR 0071): `PUT /roles/{id}` verlangt seit
# dieser Session `admin.user_management` - separates Testprincipal fuer
# `everyone_role_without` unten (siehe `_grant_role_admin_permission`).
ROLE_ADMIN_PRINCIPAL_ID = "reporting-service-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_role_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ROLE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


# Fake document ids used across the forensic-trace tests below (Post-
# Roadmap Phase 39 Session 4, ADR 0154: documents are now real
# `ResourceNode`s in permission-service, so a fake `doc-N` subject used by a
# test's fake audit event needs a REAL resource node registered for it, the
# same way `document-service` itself registers one synchronously on every
# real document creation - see `_grant_document_read_permission` below,
# which registers these with `parent_id="root"` so they inherit the same
# `document.read` grant a real document created under an ungated folder
# would). A deliberately never-registered id (`doc-unregistered`) is used
# by the "hides an unreadable entry" tests - an unregistered resource_id
# denies outright, exactly modeling a document this principal genuinely
# has no access to, with no need for a separate "never granted folder".
FAKE_DOCUMENT_IDS = ["doc-1", "doc-2", "doc-3"]


@pytest.fixture(scope="session", autouse=True)
async def _grant_document_read_permission():
    """Row-level RBAC filtering for the forensic trace (Post-Roadmap Phase
    36 Session 3) - `document.read` is NOT part of the default "everyone"
    grant set (unlike `reporting.*`), so `REPORTING_TEST_PRINCIPAL_ID` needs
    an explicit grant on `root` for the existing forensic-trace tests (whose
    fake events use `FAKE_DOCUMENT_IDS`, registered below as real resource
    nodes under `root`) to keep seeing their expected entries. Idempotent
    via a fixed role name (not the usual random-uuid throwaway pattern
    elsewhere in this project), since this fixture is session-scoped and
    must survive repeated runs against the same permission-service without
    accumulating duplicate roles. Since Post-Roadmap Phase 39 Session 4
    (ADR 0154), also registers `FAKE_DOCUMENT_IDS` as real resource nodes
    (`create_resource_node` is idempotent create-if-missing, safe to repeat
    every session) - without this, every fake `doc-N` subject would be an
    unregistered resource_id, denied outright regardless of the grant."""
    role_name = "reporting-service-test-document-read"
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role = next((r for r in roles if r["name"] == role_name), None)
        if role is None:
            created = await pc.post(
                "/roles",
                json={"name": role_name, "permissions": ["document.read"]},
                headers={"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID},
            )
            created.raise_for_status()
            role = created.json()["role"]
        existing = (
            await pc.get("/role-assignments", params={"principal_id": REPORTING_TEST_PRINCIPAL_ID})
        ).json()
        if not any(a["role_id"] == role["id"] for a in existing):
            response = await pc.post(
                "/role-assignments",
                json={
                    "principal_type": "user",
                    "principal_id": REPORTING_TEST_PRINCIPAL_ID,
                    "role_id": role["id"],
                    "resource_id": "root",
                },
            )
            response.raise_for_status()
        for document_id in FAKE_DOCUMENT_IDS:
            resource_response = await pc.post(
                "/resources",
                json={
                    "resource_id": document_id,
                    "parent_id": "root",
                    "resource_type": "document",
                },
            )
            resource_response.raise_for_status()


@pytest.fixture
def client():
    """Externe Service-Clients (workflow-/audit-/storage-/notification-
    service) durch Fakes ersetzt - identisches Muster wie folder-services
    `fake_document_client` (siehe tests/test_api.py dort): die reine
    Aggregations-/Formatierungs-Logik ist bereits in test_reports.py gegen
    diese Clients getestet, hier geht es um die Endpunkt-Verdrahtung.
    `permission_client` bleibt UNGEMOCKT (echter Aufruf gegen den laufenden
    permission-service) - der TestClient traegt daher standardmaessig einen
    `X-DMS-Principal`-Header (RBAC seit Post-Roadmap Phase 19 Session 7,
    ADR 0072; die "everyone"-Gruppe gewaehrt `reporting.read`/`.write`/
    `.forensic_trace` jedem authentifizierten Principal). Einzelne Tests
    koennen den Header per `headers={"X-DMS-Principal": ""}` ueberschreiben,
    um den Negativfall zu pruefen. `permission_client` bleibt UNGEMOCKT
    (echter Aufruf gegen den laufenden permission-service) fuer die
    Zeilenfilterung des forensischen Trace (Post-Roadmap Phase 36 Session
    3) - die bereits existierenden Tests verwenden `FAKE_DOCUMENT_IDS`
    (echt als `ResourceNode` registriert, siehe `_grant_document_read_
    permission` oben, Post-Roadmap Phase 39 Session 4/ADR 0154, seit dieser
    Session ist kein `document_client`-Mock mehr noetig - jedes `document-
    service`-Ereignis prueft direkt gegen seine eigene `resource_id`).
    `REPORTING_TEST_PRINCIPAL_ID` braucht dafuer ein echtes `document.
    read`-Grant auf `root` (anders als `reporting.*`, ist `document.read`
    NICHT Teil der "everyone"-Gruppe). `auth_client` bleibt ebenfalls echt
    (ein frischer Dev-Stack hat keinen aktiven Superuser, die
    Zeilenfilterung greift also normal)."""
    with TestClient(app, headers={"X-DMS-Principal": REPORTING_TEST_PRINCIPAL_ID}) as c:
        app.state.workflow_client = AsyncMock()
        app.state.workflow_client.list_active_instances.return_value = []
        app.state.audit_client = AsyncMock()
        app.state.audit_client.list_events.return_value = []
        app.state.storage_client = AsyncMock()
        app.state.storage_client.get_usage.return_value = []
        app.state.notification_client = AsyncMock()
        app.state.license_client = AsyncMock()
        app.state.license_client.get_status.return_value = {"installed": False, "valid": False}
        yield c


@pytest.fixture
def everyone_role_without():
    """Entfernt eine Berechtigung temporär aus der geseedeten "everyone"-
    Rolle, um den Negativpfad (fehlende Berechtigung -> 403) zu beweisen -
    gleiches Muster wie case-service/archival-service (dupliziert statt
    geteilt, Projektkonvention). Seit Post-Roadmap Phase 19 Session 6
    (ADR 0071) verlangt `PUT /roles/{id}` zusätzlich `admin.user_management`."""
    role_management_headers = {"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID}
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        everyone = next(r for r in roles if r["name"] == "everyone")
        original_permissions = list(everyone["permissions"])

        def _remove(permission: str) -> None:
            pc.put(
                f"/roles/{everyone['id']}",
                json={
                    "description": everyone["description"],
                    "permissions": [p for p in original_permissions if p != permission],
                },
                headers=role_management_headers,
            ).raise_for_status()

        yield _remove

        pc.put(
            f"/roles/{everyone['id']}",
            json={"description": everyone["description"], "permissions": original_permissions},
            headers=role_management_headers,
        ).raise_for_status()


def test_document_volume_report_without_principal_header_is_401(client):
    response = client.get("/reports/document-volume", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_document_volume_report_without_everyone_permission_is_403(client, everyone_role_without):
    everyone_role_without("reporting.read")
    response = client.get("/reports/document-volume")
    assert response.status_code == 403


def test_forensic_trace_without_everyone_permission_is_403(client, everyone_role_without):
    """`reporting.forensic_trace` ist eine separate, engere Permission als
    `reporting.read` (ADR 0072) - Entzug von `reporting.read` alleine darf
    den Forensik-Trace nicht sperren, nur der Entzug der eigenen Permission."""
    everyone_role_without("reporting.forensic_trace")
    response = client.get("/forensic-trace")
    assert response.status_code == 403


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "reporting-service"


def test_document_volume_report_empty(client):
    response = client.get("/reports/document-volume")
    assert response.status_code == 200
    assert response.json() == []


def test_document_volume_export_csv_has_correct_content_type(client):
    response = client.get("/reports/document-volume/export", params={"format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


def test_document_volume_export_pdf_has_correct_content_type(client):
    response = client.get("/reports/document-volume/export", params={"format": "pdf"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_open_workflow_tasks_report_uses_workflow_client(client):
    app.state.workflow_client.list_active_instances.return_value = [
        {"id": "i1", "process_definition_id": 1, "business_key": "bk"}
    ]
    app.state.workflow_client.list_tasks.return_value = [
        {"id": "t1", "name": "Pruefen", "lane": None}
    ]

    response = client.get("/reports/open-workflow-tasks")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["task_name"] == "Pruefen"


def test_storage_usage_report_uses_storage_client(client):
    app.state.storage_client.get_usage.return_value = [
        {"backend": "local", "object_count": 2, "total_size_bytes": 100}
    ]

    response = client.get("/reports/storage-usage")

    assert response.status_code == 200
    assert response.json() == [{"backend": "local", "object_count": 2, "total_size_bytes": 100}]


def test_license_utilization_report_uses_license_client(client):
    app.state.license_client.get_status.return_value = {
        "installed": True,
        "valid": True,
        "documents": {"limit": 1000, "current": 842, "exceeded": False},
        "storage_gb": {"limit": 50, "current": 12.3, "exceeded": False},
        "users": {"limit": 25, "current": 30, "exceeded": True},
    }

    response = client.get("/reports/license-utilization")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    by_dimension = {e["dimension"]: e for e in body}
    assert by_dimension["documents"]["current"] == 842
    assert by_dimension["users"]["exceeded"] is True


def test_license_utilization_report_without_a_license_returns_one_row(client):
    response = client.get("/reports/license-utilization")

    assert response.status_code == 200
    assert response.json() == [
        {"dimension": "license", "limit": None, "current": None, "exceeded": False}
    ]


def test_license_utilization_export_csv_has_correct_content_type(client):
    response = client.get("/reports/license-utilization/export", params={"format": "csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


def test_license_utilization_export_pdf_has_correct_content_type(client):
    response = client.get("/reports/license-utilization/export", params={"format": "pdf"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_user_activity_report_uses_audit_client(client):
    app.state.audit_client.list_events.return_value = [
        {"actor": "alice", "event_type": "document.created"},
        {"actor": "alice", "event_type": "document.created"},
    ]

    response = client.get("/reports/user-activity")

    assert response.status_code == 200
    body = response.json()
    assert body == [{"actor": "alice", "event_type": "document.created", "count": 2}]


def test_create_list_delete_schedule(client):
    created = client.post(
        "/report-schedules",
        json={
            "report_type": "storage_usage",
            "format": "csv",
            "frequency": "daily",
            "recipient_email": "admin@example.invalid",
            "filters": {},
        },
    )
    assert created.status_code == 201
    schedule_id = created.json()["id"]

    listed = client.get("/report-schedules")
    assert any(s["id"] == schedule_id for s in listed.json())

    deleted = client.delete(f"/report-schedules/{schedule_id}")
    assert deleted.status_code == 204

    listed_after = client.get("/report-schedules")
    assert all(s["id"] != schedule_id for s in listed_after.json())


def test_delete_unknown_schedule_returns_404(client):
    response = client.delete("/report-schedules/unknown-id")
    assert response.status_code == 404


def test_download_unknown_report_run_returns_404(client):
    response = client.get("/report-runs/unknown-id/download")
    assert response.status_code == 404


async def test_download_report_run_proxies_storage_client(client, session):
    """`session` (own engine bound to THIS test's event loop), not
    `app.state.session_factory` (bound to `TestClient`'s own internal
    portal loop) - same "attached to a different loop" pitfall the
    `poll_env` fixture below already documents, found here as a genuine,
    pre-existing intermittent failure while running this session's own
    (unrelated) test batch, fixed as a drive-by since it stood in the way
    of a clean test run."""
    app.state.storage_client.download.return_value = b"csv,content"

    run = await repository.create_report_run(
        session,
        schedule_id=None,
        report_type="storage_usage",
        format="csv",
        storage_object_key="reports/x/y.csv",
        content_type="text/csv",
    )
    await session.commit()

    response = client.get(f"/report-runs/{run.id}/download")

    assert response.status_code == 200
    assert response.content == b"csv,content"
    app.state.storage_client.download.assert_called_once_with("reports/x/y.csv")


def test_forensic_trace_requires_principal_header(client):
    """`queried_by` war vor Post-Roadmap Phase 19 Session 7 (ADR 0072) ein
    vom Client frei waehlbarer, ungeprueften Query-Parameter (Spoofing-
    Luecke, siehe docs/services/reporting-service.md "Offene Punkte") -
    ersetzt durch den verifizierten `X-DMS-Principal`-Header als alleinige
    Akteur-Quelle fuer die Selbst-Auditierung."""
    response = client.get("/forensic-trace", headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_forensic_trace_returns_categorized_entries(client):
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-1",
            "actor": "alice",
            "payload": {"version_number": 1},
        },
        {
            "id": 2,
            "event_type": "document.created",
            "occurred_at": "2026-08-01T09:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-1",
            "actor": "alice",
            "payload": {},
        },
    ]

    response = client.get("/forensic-trace", params={})

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 2
    categories = {e["event_type"]: e["category"] for e in body["entries"]}
    assert categories["document.downloaded"] == "download"
    assert categories["document.created"] == "change"


def test_forensic_trace_filters_by_category(client):
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-1",
            "actor": "alice",
            "payload": {},
        },
        {
            "id": 2,
            "event_type": "document.created",
            "occurred_at": "2026-08-01T09:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-1",
            "actor": "alice",
            "payload": {},
        },
    ]

    response = client.get("/forensic-trace", params={"category": "download"})

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["event_type"] == "document.downloaded"


def test_forensic_trace_reports_transparency_counts(client):
    """Row-level RBAC filtering (Post-Roadmap Phase 36 Session 3) - the
    response carries `total_before_filter`/`total_after_filter`/`superuser`
    the same way query-service's own `QueryResult` already does, so the
    admin-ui can show an "N of M visible" hint."""
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-1",
            "actor": "alice",
            "payload": {},
        }
    ]

    response = client.get("/forensic-trace", params={})

    assert response.status_code == 200
    body = response.json()
    assert body["total_before_filter"] == 1
    assert body["total_after_filter"] == 1
    assert body["superuser"] is False


def test_forensic_trace_hides_entry_resolving_to_an_unreadable_resource(client):
    """Post-Roadmap Phase 39 Session 4 (ADR 0154): a document-service event
    for a document with NO registered resource node (`doc-unregistered`,
    unlike `FAKE_DOCUMENT_IDS`) is denied outright by permission-service's
    ancestor walk - proves the row-level filter actually excludes an entry
    the caller isn't allowed to read, not just that registered entries
    happen to pass."""
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-unregistered",
            "actor": "alice",
            "payload": {},
        }
    ]

    response = client.get("/forensic-trace", params={})

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["total_before_filter"] == 1
    assert body["total_after_filter"] == 0


def test_forensic_trace_hides_entry_with_unresolvable_service(client):
    """workflow-service (and every other non-document/folder service_name)
    has no resolvable folder resource - hidden fail-closed, same boundary
    query-service's own filtering already draws."""
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "workflow.instance.completed",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "workflow-service",
            "subject": "instance-1",
            "actor": "alice",
            "payload": {},
        }
    ]

    response = client.get("/forensic-trace", params={})

    assert response.status_code == 200
    assert response.json()["entries"] == []


def test_forensic_trace_active_superuser_sees_unfiltered_entries(client, monkeypatch):
    """The activated superuser (4.6) is the only exception to row-level
    filtering (concept 6.1) - same parity as query-service's own structured
    queries."""

    async def fake_active_superuser():
        return True, REPORTING_TEST_PRINCIPAL_ID

    monkeypatch.setattr(app.state.auth_client, "get_active_superuser", fake_active_superuser)
    # Deliberately an unregistered resource - the activated superuser
    # bypasses row-level filtering entirely (no `check_batch` call at all),
    # so this document being otherwise unreadable proves the bypass, not
    # merely that a readable one happens to pass.
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-unregistered",
            "actor": "alice",
            "payload": {},
        }
    ]

    response = client.get("/forensic-trace", params={})

    assert response.status_code == 200
    body = response.json()
    assert len(body["entries"]) == 1
    assert body["superuser"] is True


def test_forensic_trace_export_excludes_unreadable_rows(client):
    """The CSV/PDF export endpoint reuses the same `_fetch_forensic_trace`
    filtering - a caller cannot bypass row-level RBAC by exporting instead
    of viewing."""
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "event_type": "document.downloaded",
            "occurred_at": "2026-08-01T10:00:00+00:00",
            "service_name": "document-service",
            "subject": "doc-unregistered",
            "actor": "alice",
            "payload": {},
        }
    ]

    response = client.get("/forensic-trace/export", params={"format": "csv"})

    assert response.status_code == 200
    assert "doc-unregistered" not in response.text


def test_forensic_trace_reports_download_anomaly(client):
    from reporting_service.main import settings as reporting_settings

    app.state.audit_client.list_events.return_value = [
        {
            "id": i,
            "event_type": "document.downloaded",
            "occurred_at": f"2026-08-01T10:00:0{i}+00:00",
            "service_name": "document-service",
            "subject": f"doc-{i}",
            "actor": "alice",
            "payload": {},
        }
        for i in range(1, 3)
    ]
    # Wenige echte Events reichen fuer den Test - Schwellwert temporaer senken.
    reporting_settings.anomaly_download_threshold_count = 2
    try:
        response = client.get("/forensic-trace", params={})
    finally:
        reporting_settings.anomaly_download_threshold_count = 20

    assert response.status_code == 200
    assert response.json()["anomalies"] != []
    assert "alice" in response.json()["anomalies"][0]


def test_forensic_trace_export_csv(client):
    app.state.audit_client.list_events.return_value = []

    response = client.get("/forensic-trace/export", params={"format": "csv"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")


def test_forensic_trace_export_pdf(client):
    app.state.audit_client.list_events.return_value = []

    response = client.get("/forensic-trace/export", params={"format": "pdf"})

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_forensic_trace_query_is_self_audited(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)
    app.state.audit_client.list_events.return_value = []

    client.get(
        "/forensic-trace",
        params={"actor": "alice", "subject": "doc-1"},
    )

    trace_events = [e for e in published if e.event_type == "reporting.forensic_trace.queried"]
    assert len(trace_events) == 1
    assert trace_events[0].actor == REPORTING_TEST_PRINCIPAL_ID
    assert trace_events[0].payload["actor"] == "alice"
    assert trace_events[0].payload["subject"] == "doc-1"


@pytest.fixture
def poll_env(engine):
    """`_run_due_schedules` gegen eine eigene, im aktuellen Test-Event-Loop
    erzeugte Engine statt `app.state.session_factory` (das via `TestClient`
    in dessen eigenem internen Loop entsteht) - sonst "attached to a
    different loop"-Fehler von asyncpg, da async-Testfunktionen in einem
    eigenen pytest-asyncio-Loop laufen. `app.state.*_client` werden hier
    direkt gemockt, ganz ohne `TestClient`/Lifespan noetig, da
    `_run_due_schedules` nur `repository`-Funktionen und `app.state.storage_
    client`/`notification_client` braucht."""
    session_factory = make_session_factory(engine)
    app.state.storage_client = AsyncMock()
    app.state.storage_client.get_usage.return_value = [
        {"backend": "local", "object_count": 1, "total_size_bytes": 10}
    ]
    app.state.notification_client = AsyncMock()
    return session_factory


async def test_poll_tick_executes_due_schedule_and_sends_notification(poll_env):
    session_factory = poll_env
    async with session_factory() as session:
        schedule = await repository.create_schedule(
            session,
            report_type="storage_usage",
            format="csv",
            frequency="daily",
            recipient_email="admin@example.invalid",
            filters={},
        )
        schedule.next_run_at = datetime(2020, 1, 1, tzinfo=UTC)
        await session.commit()
        schedule_id = schedule.id

    await _run_due_schedules(session_factory)

    app.state.notification_client.send_email.assert_called_once()
    app.state.storage_client.upload.assert_called_once()

    async with session_factory() as session:
        updated = await repository.get_schedule(session, schedule_id)
        assert updated.last_run_at is not None
        assert updated.next_run_at > datetime(2020, 1, 1, tzinfo=UTC)


async def test_poll_tick_skips_schedules_that_are_not_due_yet(poll_env):
    session_factory = poll_env
    async with session_factory() as session:
        schedule = await repository.create_schedule(
            session,
            report_type="storage_usage",
            format="csv",
            frequency="daily",
            recipient_email="admin@example.invalid",
            filters={},
        )
        schedule.next_run_at = datetime(2099, 1, 1, tzinfo=UTC)
        await session.commit()

    await _run_due_schedules(session_factory)

    app.state.notification_client.send_email.assert_not_called()
