from datetime import UTC, datetime, timedelta

import pytest
from permission_service import repository
from permission_service.models import ResourceNode
from permission_service.settings import ROOT_RESOURCE_ID


async def _add_child(session, resource_id, parent_id=ROOT_RESOURCE_ID, inherit=True):
    session.add(ResourceNode(resource_id=resource_id, parent_id=parent_id, inherit=inherit))
    await session.flush()


async def test_create_role(session):
    role = await repository.create_role(session, "Editor", "kann bearbeiten", ["read", "write"])

    assert role.id is not None
    assert role.permissions == ["read", "write"]


async def test_role_assignment_at_root_is_inherited_everywhere(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await _add_child(session, "folder-a")
    await _add_child(session, "folder-a-sub", parent_id="folder-a")

    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )

    entry = await repository.get_effective_permissions(session, "alice", "folder-a-sub")

    assert entry.permissions == ["read"]
    assert entry.roles == ["Viewer"]


async def test_assignment_deeper_in_tree_adds_to_inherited(session):
    viewer = await repository.create_role(session, "Viewer", "", ["read"])
    editor = await repository.create_role(session, "Editor", "", ["write"])
    await _add_child(session, "folder-a")

    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=viewer.id,
        resource_id=ROOT_RESOURCE_ID,
    )
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=editor.id,
        resource_id="folder-a",
    )

    entry = await repository.get_effective_permissions(session, "alice", "folder-a")

    assert set(entry.permissions) == {"read", "write"}


async def test_broken_inheritance_stops_at_that_node(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await _add_child(session, "folder-a", inherit=False)

    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )

    entry = await repository.get_effective_permissions(session, "alice", "folder-a")

    assert entry.permissions == []
    assert entry.roles == []


async def test_broken_inheritance_still_honors_own_assignment(session):
    role = await repository.create_role(session, "Editor", "", ["write"])
    await _add_child(session, "folder-a", inherit=False)

    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id="folder-a",
    )

    entry = await repository.get_effective_permissions(session, "alice", "folder-a")

    assert entry.permissions == ["write"]


async def test_other_principal_unaffected(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )

    entry = await repository.get_effective_permissions(session, "bob", ROOT_RESOURCE_ID)

    assert entry.permissions == []


async def test_cache_is_reused_on_second_call(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )

    first = await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)
    second = await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)

    assert first.computed_at == second.computed_at


async def test_new_assignment_invalidates_cache(session):
    viewer = await repository.create_role(session, "Viewer", "", ["read"])
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=viewer.id,
        resource_id=ROOT_RESOURCE_ID,
    )
    await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)

    editor = await repository.create_role(session, "Editor", "", ["write"])
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=editor.id,
        resource_id=ROOT_RESOURCE_ID,
    )

    entry = await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)
    assert set(entry.permissions) == {"read", "write"}


async def test_delete_assignment_invalidates_cache(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    assignment = await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )
    await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)

    await repository.delete_role_assignment(session, assignment.id)

    entry = await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)
    assert entry.permissions == []


async def test_set_resource_inherit_invalidates_cache(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await _add_child(session, "folder-a")
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id="alice",
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )
    before = await repository.get_effective_permissions(session, "alice", "folder-a")
    assert before.permissions == ["read"]

    await repository.set_resource_inherit(session, "folder-a", inherit=False)

    after = await repository.get_effective_permissions(session, "alice", "folder-a")
    assert after.permissions == []


async def test_create_scope_lock_with_unknown_resource_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.create_scope_lock(
            session,
            resource_id="does-not-exist",
            locked_by="admin",
            reason=None,
            blocks_read=False,
            expires_at=None,
        )


async def test_scope_lock_blocks_subtree_regardless_of_inherit_flag(session):
    await _add_child(session, "folder-a", inherit=False)
    await _add_child(session, "folder-a-sub", parent_id="folder-a")
    await repository.create_scope_lock(
        session,
        resource_id="folder-a",
        locked_by="admin",
        reason="Migration",
        blocks_read=False,
        expires_at=None,
    )

    active = await repository.get_active_scope_locks_for_resource(session, "folder-a-sub")

    assert len(active) == 1
    assert active[0].reason == "Migration"


async def test_scope_lock_at_root_blocks_entire_tree(session):
    await _add_child(session, "folder-a")
    await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=None,
    )

    active = await repository.get_active_scope_locks_for_resource(session, "folder-a")

    assert len(active) == 1


async def test_expired_scope_lock_is_not_active(session):
    await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    active = await repository.get_active_scope_locks_for_resource(session, ROOT_RESOURCE_ID)

    assert active == []


async def test_release_scope_lock_makes_it_inactive(session):
    lock = await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=None,
    )

    released = await repository.release_scope_lock(session, lock.id, "admin2")

    assert released.released_by == "admin2"
    active = await repository.get_active_scope_locks_for_resource(session, ROOT_RESOURCE_ID)
    assert active == []


async def test_release_unknown_scope_lock_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.release_scope_lock(session, 999999, "admin")


async def test_release_already_released_scope_lock_raises(session):
    lock = await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=None,
    )
    await repository.release_scope_lock(session, lock.id, "admin")

    with pytest.raises(repository.NotFoundError):
        await repository.release_scope_lock(session, lock.id, "admin")


async def test_list_scope_locks_filters_by_resource(session):
    await _add_child(session, "folder-a")
    await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=None,
    )
    await repository.create_scope_lock(
        session,
        resource_id="folder-a",
        locked_by="admin",
        reason=None,
        blocks_read=False,
        expires_at=None,
    )

    all_locks = await repository.list_scope_locks(session, None)
    scoped = await repository.list_scope_locks(session, "folder-a")

    assert len(all_locks) == 2
    assert len(scoped) == 1
    assert scoped[0].resource_id == "folder-a"


async def test_get_approval_config_defaults_to_false_when_unconfigured(session):
    config = await repository.get_approval_config(session, "document.force_unlock")

    assert config.requires_approval is False


async def test_set_approval_config_persists_and_is_read_back(session):
    await repository.set_approval_config(session, "document.force_unlock", requires_approval=True)

    config = await repository.get_approval_config(session, "document.force_unlock")

    assert config.requires_approval is True


async def test_set_approval_config_upsert_updates_existing_row(session):
    await repository.set_approval_config(session, "document.force_unlock", requires_approval=True)
    await repository.set_approval_config(session, "document.force_unlock", requires_approval=False)

    configs = await repository.list_approval_configs(session)

    assert len(configs) == 1
    assert configs[0].requires_approval is False


async def test_create_and_get_approval_request(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={"x": 1}
    )

    fetched = await repository.get_approval_request(session, request.id)

    assert fetched.status == "pending"
    assert fetched.payload == {"x": 1}


async def test_get_unknown_approval_request_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_approval_request(session, "does-not-exist")


async def test_list_approval_requests_filters_by_status_and_action_type(session):
    await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )
    approved = await repository.create_approval_request(
        session, action_type="permission.scope_lock.create", initiated_by="bob", payload={}
    )
    await repository.approve_request(session, approved.id, approved_by="carol")

    all_requests = await repository.list_approval_requests(session)
    pending_only = await repository.list_approval_requests(session, status="pending")
    by_type = await repository.list_approval_requests(
        session, action_type="permission.scope_lock.create"
    )

    assert len(all_requests) == 2
    assert {r.action_type for r in pending_only} == {"document.force_unlock"}
    assert [r.id for r in by_type] == [approved.id]


async def test_approve_request_rejects_initiator_as_approver(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )

    with pytest.raises(repository.NotInitiatorAllowedError):
        await repository.approve_request(session, request.id, approved_by="alice")


async def test_approve_request_by_different_person_succeeds(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )

    approved = await repository.approve_request(session, request.id, approved_by="bob")

    assert approved.status == "approved"
    assert approved.approved_by == "bob"
    assert approved.decided_at is not None


async def test_approve_already_decided_request_raises_not_pending(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )
    await repository.approve_request(session, request.id, approved_by="bob")

    with pytest.raises(repository.ApprovalRequestNotPendingError):
        await repository.approve_request(session, request.id, approved_by="carol")


async def test_reject_request_allows_initiator_to_withdraw_own_request(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )

    rejected = await repository.reject_request(
        session, request.id, rejected_by="alice", reason="Doch nicht nötig"
    )

    assert rejected.status == "rejected"
    assert rejected.rejected_by == "alice"
    assert rejected.reason == "Doch nicht nötig"


async def test_reject_already_decided_request_raises_not_pending(session):
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )
    await repository.reject_request(session, request.id, rejected_by="bob", reason=None)

    with pytest.raises(repository.ApprovalRequestNotPendingError):
        await repository.reject_request(session, request.id, rejected_by="carol", reason=None)
