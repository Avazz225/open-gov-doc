from datetime import UTC, datetime

from reporting_service.filtering import RESULT_READ_PERMISSION, filter_entries_by_permission
from reporting_service.schemas import ForensicTraceEntry


class FakeDocumentClient:
    def __init__(self, folder_by_document_id: dict[str, str | None]):
        self._folder_by_document_id = folder_by_document_id
        self.calls: list[str] = []

    async def get_document(self, document_id: str) -> dict | None:
        self.calls.append(document_id)
        if document_id not in self._folder_by_document_id:
            return None
        return {"id": document_id, "folder_id": self._folder_by_document_id[document_id]}


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


async def test_document_entry_visible_when_folder_allowed():
    entries = [_document_entry(1, "doc-1")]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-a"})

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == entries
    assert permission_client.calls[0][1] == RESULT_READ_PERMISSION


async def test_document_entry_hidden_when_folder_not_allowed():
    entries = [_document_entry(1, "doc-1")]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []


async def test_folder_entry_uses_subject_as_resource_id_directly():
    entries = [_folder_entry(1, "folder-b")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-b"})

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == entries
    assert document_client.calls == []


async def test_unresolvable_entry_hidden_for_regular_principal():
    entries = [_entry(entry_id=1, service_name="workflow-service", subject="instance-1")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"anything"})

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []
    assert permission_client.calls == []


async def test_unresolvable_entry_visible_for_superuser():
    entries = [_entry(entry_id=1, service_name="workflow-service", subject="instance-1")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_entries_by_permission(
        entries,
        principal_id="superuser-1",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=True,
    )

    assert result == entries
    assert permission_client.calls == []


async def test_missing_document_treated_as_unresolvable():
    entries = [_document_entry(1, "doc-deleted")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"any-folder"})

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []


async def test_duplicate_document_subjects_resolved_only_once():
    entries = [
        _document_entry(1, "doc-1"),
        _document_entry(2, "doc-1"),
        _document_entry(3, "doc-1"),
    ]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-a"})

    result = await filter_entries_by_permission(
        entries,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == entries
    assert document_client.calls == ["doc-1"]
