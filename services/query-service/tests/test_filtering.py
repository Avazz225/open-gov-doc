from query_service.filtering import RESULT_READ_PERMISSION, filter_events_by_permission


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


def _document_event(document_id: str, subject: str | None = None) -> dict:
    return {"service_name": "document-service", "subject": subject or document_id}


def _folder_event(resource_id: str) -> dict:
    return {"service_name": "folder-service", "subject": resource_id}


async def test_document_event_visible_when_folder_allowed():
    events = [_document_event("doc-1")]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-a"})

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == events
    assert permission_client.calls[0][1] == RESULT_READ_PERMISSION


async def test_document_event_hidden_when_folder_not_allowed():
    events = [_document_event("doc-1")]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []


async def test_folder_event_uses_subject_as_resource_id_directly():
    events = [_folder_event("folder-b")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-b"})

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == events
    assert document_client.calls == []


async def test_unresolvable_event_hidden_for_regular_principal():
    events = [{"service_name": "workflow-service", "subject": "instance-1"}]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"anything"})

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []
    assert permission_client.calls == []


async def test_unresolvable_event_visible_for_superuser():
    events = [{"service_name": "workflow-service", "subject": "instance-1"}]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_events_by_permission(
        events,
        principal_id="superuser-1",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=True,
    )

    assert result == events
    assert permission_client.calls == []


async def test_missing_document_treated_as_unresolvable():
    events = [_document_event("doc-deleted")]
    document_client = FakeDocumentClient({})
    permission_client = FakePermissionClient(allowed_resource_ids={"any-folder"})

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == []


async def test_duplicate_document_subjects_resolved_only_once():
    events = [_document_event("doc-1"), _document_event("doc-1"), _document_event("doc-1")]
    document_client = FakeDocumentClient({"doc-1": "folder-a"})
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-a"})

    result = await filter_events_by_permission(
        events,
        principal_id="alice",
        permission_client=permission_client,
        document_client=document_client,
        is_superuser=False,
    )

    assert result == events
    assert document_client.calls == ["doc-1"]
