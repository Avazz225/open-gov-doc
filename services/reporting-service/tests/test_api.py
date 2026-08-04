from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from dms_db_base import make_session_factory
from fastapi.testclient import TestClient
from reporting_service import repository
from reporting_service.main import _run_due_schedules, app


@pytest.fixture
def client():
    """Externe Service-Clients (workflow-/audit-/storage-/notification-
    service) durch Fakes ersetzt - identisches Muster wie folder-services
    `fake_document_client` (siehe tests/test_api.py dort): die reine
    Aggregations-/Formatierungs-Logik ist bereits in test_reports.py gegen
    diese Clients getestet, hier geht es um die Endpunkt-Verdrahtung."""
    with TestClient(app) as c:
        app.state.workflow_client = AsyncMock()
        app.state.workflow_client.list_active_instances.return_value = []
        app.state.audit_client = AsyncMock()
        app.state.audit_client.list_events.return_value = []
        app.state.storage_client = AsyncMock()
        app.state.storage_client.get_usage.return_value = []
        app.state.notification_client = AsyncMock()
        yield c


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


async def test_download_report_run_proxies_storage_client(client):
    app.state.storage_client.download.return_value = b"csv,content"

    async with app.state.session_factory() as session:
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
