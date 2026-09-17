from query_service.filtering import RESULT_READ_PERMISSION, filter_events_by_permission


class FakePermissionClient:
    def __init__(self, allowed_resource_ids: set[str]):
        self._allowed = allowed_resource_ids
        self.calls: list[tuple] = []

    async def check_batch(
        self, *, principal_id: str, permission: str, access_type: str, resource_ids: list[str]
    ) -> dict[str, bool]:
        self.calls.append((principal_id, permission, access_type, tuple(sorted(resource_ids))))
        return {rid: rid in self._allowed for rid in resource_ids}


def _document_event(document_id: str) -> dict:
    return {"service_name": "document-service", "subject": document_id}


def _folder_event(resource_id: str) -> dict:
    return {"service_name": "folder-service", "subject": resource_id}


async def test_document_event_visible_when_its_own_resource_allowed():
    """Post-Roadmap Phase 39 Session 4 (ADR 0154): a `document-service`
    event's `subject` (the document's own id) is now checked directly as
    its own `resource_id` - no more lookup to a containing folder."""
    events = [_document_event("doc-1")]
    permission_client = FakePermissionClient(allowed_resource_ids={"doc-1"})

    result = await filter_events_by_permission(
        events, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == events
    assert permission_client.calls[0][1] == RESULT_READ_PERMISSION
    assert permission_client.calls[0][3] == ("doc-1",)


async def test_document_event_hidden_when_its_own_resource_not_allowed():
    events = [_document_event("doc-1")]
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_events_by_permission(
        events, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == []


async def test_folder_event_uses_subject_as_resource_id_directly():
    events = [_folder_event("folder-b")]
    permission_client = FakePermissionClient(allowed_resource_ids={"folder-b"})

    result = await filter_events_by_permission(
        events, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == events


async def test_unresolvable_event_hidden_for_regular_principal():
    events = [{"service_name": "workflow-service", "subject": "instance-1"}]
    permission_client = FakePermissionClient(allowed_resource_ids={"anything"})

    result = await filter_events_by_permission(
        events, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == []
    assert permission_client.calls == []


async def test_unresolvable_event_visible_for_superuser():
    events = [{"service_name": "workflow-service", "subject": "instance-1"}]
    permission_client = FakePermissionClient(allowed_resource_ids=set())

    result = await filter_events_by_permission(
        events, principal_id="superuser-1", permission_client=permission_client, is_superuser=True
    )

    assert result == events
    assert permission_client.calls == []


async def test_duplicate_document_subjects_deduplicated_in_check_batch_call():
    events = [_document_event("doc-1"), _document_event("doc-1"), _document_event("doc-1")]
    permission_client = FakePermissionClient(allowed_resource_ids={"doc-1"})

    result = await filter_events_by_permission(
        events, principal_id="alice", permission_client=permission_client, is_superuser=False
    )

    assert result == events
    assert permission_client.calls[0][3] == ("doc-1",)
