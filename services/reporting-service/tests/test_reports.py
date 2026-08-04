from datetime import UTC, datetime

from reporting_service import reports
from reporting_service.schemas import DocumentVolumeEntry


class FakeWorkflowClient:
    def __init__(self, instances, tasks_by_instance):
        self._instances = instances
        self._tasks_by_instance = tasks_by_instance

    async def list_active_instances(self):
        return self._instances

    async def list_tasks(self, instance_id):
        return self._tasks_by_instance.get(instance_id, [])


class FakeStorageClient:
    def __init__(self, usage):
        self._usage = usage

    async def get_usage(self):
        return self._usage


class FakeAuditClient:
    def __init__(self, events):
        self._events = events

    async def list_events(self, *, actor=None, since=None, until=None, limit=5000):
        return self._events


async def test_document_volume_delegates_to_repository(session):
    from reporting_service import repository

    await repository.record_document_created(
        session, document_id="doc-1", folder_id="f1", occurred_at=datetime(2026, 1, 1, tzinfo=UTC)
    )

    entries = await reports.document_volume(
        session, since=None, until=None, folder_id=None, group_by="day"
    )

    assert entries == [DocumentVolumeEntry(period="2026-01-01", folder_id="f1", count=1)]


async def test_open_workflow_tasks_flattens_instances_and_tasks():
    client = FakeWorkflowClient(
        instances=[{"id": "i1", "process_definition_id": 3, "business_key": "bk-1"}],
        tasks_by_instance={
            "i1": [{"id": "t1", "name": "Freigabe", "lane": "Manager"}],
        },
    )

    entries = await reports.open_workflow_tasks(client)

    assert len(entries) == 1
    assert entries[0].instance_id == "i1"
    assert entries[0].task_name == "Freigabe"
    assert entries[0].lane == "Manager"


async def test_open_workflow_tasks_empty_without_active_instances():
    client = FakeWorkflowClient(instances=[], tasks_by_instance={})

    entries = await reports.open_workflow_tasks(client)

    assert entries == []


async def test_storage_usage_maps_raw_entries():
    client = FakeStorageClient(
        usage=[{"backend": "local", "object_count": 3, "total_size_bytes": 900}]
    )

    entries = await reports.storage_usage(client)

    assert entries[0].backend == "local"
    assert entries[0].object_count == 3


async def test_user_activity_aggregates_by_actor_and_event_type():
    client = FakeAuditClient(
        events=[
            {"actor": "alice", "event_type": "document.created"},
            {"actor": "alice", "event_type": "document.created"},
            {"actor": "alice", "event_type": "document.deleted"},
            {"actor": "bob", "event_type": "document.created"},
            {"actor": None, "event_type": "registry.instance.registered"},
        ]
    )

    entries = await reports.user_activity(client, actor=None, since=None, until=None)

    by_key = {(e.actor, e.event_type): e.count for e in entries}
    assert by_key[("alice", "document.created")] == 2
    assert by_key[("alice", "document.deleted")] == 1
    assert by_key[("bob", "document.created")] == 1
    assert ("None", "registry.instance.registered") not in by_key
    assert len(entries) == 3


def test_to_csv_produces_header_and_rows():
    content = reports.to_csv(["a", "b"], [["1", "2"], ["3", "4"]])

    text = content.decode("utf-8")
    assert "a,b" in text
    assert "1,2" in text
    assert "3,4" in text


def test_to_pdf_produces_nonempty_pdf_bytes():
    content = reports.to_pdf("Test-Bericht", ["a", "b"], [["1", "2"]])

    assert content.startswith(b"%PDF")
    assert len(content) > 100
