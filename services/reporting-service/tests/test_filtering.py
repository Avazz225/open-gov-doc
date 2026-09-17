from datetime import UTC, datetime

from reporting_service.filtering import RESULT_READ_PERMISSION, filter_entries_by_permission
from reporting_service.schemas import ForensicTraceEntry


class FakePermissionClient:
    def __init__(self, allowed_resource_ids: set[str]):
        self._allowed = allowed_resource_ids
        self.calls: list[tuple] = []

    async def check_batch(
        self, *, principal_id: str, permission: str, access_type: str, resource_ids: list[str]
    ) -> dict[str, bool]:
        self.calls.append((principal_id, permission, access_type, tuple(sorted(resource_ids))))
        return {rid: rid in self._allowed for rid in resource_ids}


def _entry(*, entry_id: int, service_name: str, subject: str | None) -> ForensicTraceEntry:
    return ForensicTraceEntry(
        id=entry_id,
        event_type="document.viewed",
        category="view",
        occurred_at=datetime.now(UTC),
        service_name=service_name,
        subject=subject,
        actor="alice",
        payload={},
    )


def _document_entry(entry_id: int, document_id: str) -> ForensicTraceEntry:
    return _entry(entry_id=entry_id, service_name="document-service", subject=document_id)


def _folder_entry(entry_id: int, resource_id: str) -> ForensicTraceEntry:
    return _entry(entry_id=entry_id, service_name="folder-service", subject=resource_id)


async def test_document_entry_visible_when_its_own_resource_allowed():
    """Post-Roadmap Phase 39 Session 4 (ADR 0154): a `document-service`
    entry's `subject` (the document's own id) is now checked directly as
    its own `resource_id` - no more lookup to a containing folder."""
    entries = [_document_entry(1, "doc-1")]
    permission_client = FakePermissionClient(allowed_resource_ids={"doc-1"})

    result = await filter_entries_by_permission(
        entries, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == entries
    assert permission_client.calls[0][1] == RESULT_READ_PERMISSION
    assert permission_client.calls[0][3] == ("doc-1",)


async def test_document_entry_hidden_when_its_own_resource_not_allowed():
    entries = [_document_entry(1, "doc-1")]
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_entries_by_permission(
        entries, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == []


async def test_folder_entry_uses_subject_as_resource_id_directly():
    entries = [_folder_entry(1, "folder-b")]
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-b"})

    result = await filter_entries_by_permission(
        entries, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == entries


async def test_unresolvable_entry_hidden_for_regular_principal():
    entries = [_entry(entry_id=1, service_name="workflow-service", subject="instance-1")]
    permission_client = FakePermissionClient(allowed_resource_ids={"anything"})

    result = await filter_entries_by_permission(
        entries, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == []
    assert permission_client.calls == []


async def test_unresolvable_entry_visible_for_superuser():
    entries = [_entry(entry_id=1, service_name="workflow-service", subject="instance-1")]
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_entries_by_permission(
        entries,
        principal_id="superuser-1",
        permission_client=permission_client,
        is_superuser=True,
    )

    assert result == entries
    assert permission_client.calls == []


async def test_duplicate_document_subjects_deduplicated_in_check_batch_call():
    entries = [
        _document_entry(1, "doc-1"),
        _document_entry(2, "doc-1"),
        _document_entry(3, "doc-1"),
    ]
    permission_client = FakePermissionClient(allowed_resource_ids={"doc-1"})

    result = await filter_entries_by_permission(
        entries, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == entries
    assert permission_client.calls[0][3] == ("doc-1",)
