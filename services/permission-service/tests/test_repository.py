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


async def test_ensure_domain_admin_roles_seeds_all_expected_roles(session):
    await repository.ensure_domain_admin_roles(session)

    roles = await repository.list_roles(session)
    names = {role.name for role in roles}

    assert names == {name for name, _, _ in repository.DOMAIN_ADMIN_ROLES}
    users_role = await repository.get_role_by_name(session, "domain-admin-users")
    assert users_role.permissions == ["admin.user_management"]
    breakglass_role = await repository.get_role_by_name(session, "breakglass-approver")
    assert breakglass_role.permissions == ["breakglass.approve"]
    emergency_role = await repository.get_role_by_name(session, "domain-admin-emergency")
    assert emergency_role.permissions == ["system.not_shutdown.trigger"]


async def test_ensure_domain_admin_roles_is_idempotent(session):
    await repository.ensure_domain_admin_roles(session)
    await repository.ensure_domain_admin_roles(session)

    roles = await repository.list_roles(session)

    assert len(roles) == len(repository.DOMAIN_ADMIN_ROLES)


async def test_get_role_by_name_returns_none_when_missing(session):
    assert await repository.get_role_by_name(session, "does-not-exist") is None


async def _grant_permission(session, *, principal_id, role_name, permissions):
    role = await repository.create_role(session, role_name, "", permissions)
    await repository.create_role_assignment(
        session,
        principal_type="user",
        principal_id=principal_id,
        role_id=role.id,
        resource_id=ROOT_RESOURCE_ID,
    )


async def test_create_approval_request_rejects_initiator_without_required_permission(session):
    await repository.set_approval_config(
        session,
        "auth.superuser.activate",
        requires_approval=True,
        required_permission="breakglass.approve",
    )

    with pytest.raises(repository.MissingRequiredPermissionError):
        await repository.create_approval_request(
            session, action_type="auth.superuser.activate", initiated_by="alice", payload={}
        )


async def test_create_approval_request_allows_initiator_with_required_permission(session):
    await repository.set_approval_config(
        session,
        "auth.superuser.activate",
        requires_approval=True,
        required_permission="breakglass.approve",
    )
    await _grant_permission(
        session, principal_id="alice", role_name="approver", permissions=["breakglass.approve"]
    )

    request = await repository.create_approval_request(
        session, action_type="auth.superuser.activate", initiated_by="alice", payload={}
    )

    assert request.status == "pending"


async def test_approve_request_rejects_approver_without_required_permission(session):
    await repository.set_approval_config(
        session,
        "auth.superuser.activate",
        requires_approval=True,
        required_permission="breakglass.approve",
    )
    await _grant_permission(
        session, principal_id="alice", role_name="approver1", permissions=["breakglass.approve"]
    )
    request = await repository.create_approval_request(
        session, action_type="auth.superuser.activate", initiated_by="alice", payload={}
    )

    with pytest.raises(repository.MissingRequiredPermissionError):
        await repository.approve_request(session, request.id, approved_by="bob")


async def test_approve_request_allows_approver_with_required_permission(session):
    await repository.set_approval_config(
        session,
        "auth.superuser.activate",
        requires_approval=True,
        required_permission="breakglass.approve",
    )
    await _grant_permission(
        session, principal_id="alice", role_name="approver1", permissions=["breakglass.approve"]
    )
    await _grant_permission(
        session, principal_id="bob", role_name="approver2", permissions=["breakglass.approve"]
    )
    request = await repository.create_approval_request(
        session, action_type="auth.superuser.activate", initiated_by="alice", payload={}
    )

    approved = await repository.approve_request(session, request.id, approved_by="bob")

    assert approved.status == "approved"


async def test_required_permission_none_does_not_affect_existing_action_types(session):
    """Regression: `required_permission=None` (Default) darf bestehende,
    ungegatete Aktionstypen (Scope-Lock/Force-Unlock, P6-S4) nicht
    beeinträchtigen."""
    request = await repository.create_approval_request(
        session, action_type="document.force_unlock", initiated_by="alice", payload={}
    )

    approved = await repository.approve_request(session, request.id, approved_by="bob")

    assert approved.status == "approved"


async def test_require_capability_raises_when_missing(session):
    with pytest.raises(repository.MissingRequiredPermissionError):
        await repository.require_capability(session, "alice", "system.not_shutdown.trigger")


async def test_require_capability_passes_when_held(session):
    await _grant_permission(
        session,
        principal_id="alice",
        role_name="emergency",
        permissions=["system.not_shutdown.trigger"],
    )

    await repository.require_capability(session, "alice", "system.not_shutdown.trigger")


async def test_get_or_seed_maintenance_mode_defaults_to_inactive(session):
    mode = await repository.get_or_seed_maintenance_mode(session)

    assert mode.active is False
    assert mode.triggered_by is None


async def test_get_or_seed_maintenance_mode_is_idempotent(session):
    first = await repository.get_or_seed_maintenance_mode(session)
    second = await repository.get_or_seed_maintenance_mode(session)

    assert first.id == second.id == repository.MAINTENANCE_MODE_ID


async def test_activate_maintenance_mode_sets_fields(session):
    mode = await repository.activate_maintenance_mode(
        session, triggered_by="alice", reason="Verdacht auf unautorisierten Zugriff"
    )

    assert mode.active is True
    assert mode.triggered_by == "alice"
    assert mode.reason == "Verdacht auf unautorisierten Zugriff"
    assert mode.activated_at is not None
    assert mode.lifted_by is None


async def test_lift_maintenance_mode_clears_active_flag(session):
    await repository.activate_maintenance_mode(session, triggered_by="alice", reason=None)

    mode = await repository.lift_maintenance_mode(session, lifted_by="superuser-id")

    assert mode.active is False
    assert mode.lifted_by == "superuser-id"
    assert mode.lifted_at is not None


async def test_reactivating_after_lift_clears_previous_lift_fields(session):
    await repository.activate_maintenance_mode(session, triggered_by="alice", reason=None)
    await repository.lift_maintenance_mode(session, lifted_by="superuser-id")

    mode = await repository.activate_maintenance_mode(session, triggered_by="bob", reason="Erneut")

    assert mode.active is True
    assert mode.triggered_by == "bob"
    assert mode.lifted_by is None
    assert mode.lifted_at is None


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


async def _create_delegation(
    session,
    *,
    delegator="alice",
    deputy="bob",
    starts_delta=timedelta(days=-1),
    ends_delta=timedelta(days=1),
    scope_object_type_ids=None,
    scope_process_definition_ids=None,
    scope_folder_resource_ids=None,
):
    now = datetime.now(UTC)
    return await repository.create_delegation(
        session,
        delegator_principal_id=delegator,
        deputy_principal_id=deputy,
        starts_at=now + starts_delta,
        ends_at=now + ends_delta,
        scope_object_type_ids=scope_object_type_ids,
        scope_process_definition_ids=scope_process_definition_ids,
        scope_folder_resource_ids=scope_folder_resource_ids,
    )


async def test_create_delegation_roundtrips(session):
    delegation = await _create_delegation(session)

    fetched = await repository.get_delegation(session, delegation.id)

    assert fetched is not None
    assert fetched.delegator_principal_id == "alice"
    assert fetched.deputy_principal_id == "bob"
    assert fetched.revoked_at is None


async def test_list_delegations_filters_by_delegator_and_deputy(session):
    await _create_delegation(session, delegator="alice", deputy="bob")
    await _create_delegation(session, delegator="alice", deputy="carol")
    await _create_delegation(session, delegator="dave", deputy="bob")

    for_alice = await repository.list_delegations(session, delegator_principal_id="alice")
    assert len(for_alice) == 2

    for_bob = await repository.list_delegations(session, deputy_principal_id="bob")
    assert len(for_bob) == 2


async def test_list_delegations_active_only_excludes_revoked_and_out_of_window(session):
    active = await _create_delegation(session, delegator="alice", deputy="bob")
    await _create_delegation(
        session,
        delegator="alice",
        deputy="carol",
        starts_delta=timedelta(days=5),
        ends_delta=timedelta(days=10),
    )
    revoked = await _create_delegation(session, delegator="alice", deputy="dave")
    await repository.revoke_delegation(session, revoked.id, revoked_by="alice")

    results = await repository.list_delegations(
        session, delegator_principal_id="alice", active_only=True
    )

    assert [d.id for d in results] == [active.id]


async def test_revoke_delegation_is_idempotent_and_keeps_first_revoker(session):
    delegation = await _create_delegation(session)

    await repository.revoke_delegation(session, delegation.id, revoked_by="alice")
    twice = await repository.revoke_delegation(session, delegation.id, revoked_by="admin")

    assert twice.revoked_by == "alice"


async def test_revoke_delegation_unknown_id_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.revoke_delegation(session, "does-not-exist", revoked_by="alice")


async def test_is_delegation_active_false_when_before_window(session):
    now = datetime.now(UTC)
    delegation = repository.Delegation(
        id="d1",
        delegator_principal_id="alice",
        deputy_principal_id="bob",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=2),
        scope_object_type_ids=None,
        scope_process_definition_ids=None,
        scope_folder_resource_ids=None,
        created_at=now,
    )
    assert repository.is_delegation_active(delegation, now) is False


async def test_is_delegation_active_false_when_after_window(session):
    now = datetime.now(UTC)
    delegation = repository.Delegation(
        id="d1",
        delegator_principal_id="alice",
        deputy_principal_id="bob",
        starts_at=now - timedelta(days=2),
        ends_at=now - timedelta(days=1),
        scope_object_type_ids=None,
        scope_process_definition_ids=None,
        scope_folder_resource_ids=None,
        created_at=now,
    )
    assert repository.is_delegation_active(delegation, now) is False


async def test_is_active_deputy_for_true_without_scope_restriction(session):
    await _create_delegation(session, delegator="alice", deputy="bob")

    assert await repository.is_active_deputy_for(
        session, deputy_principal_id="bob", delegator_principal_id="alice"
    )


async def test_is_active_deputy_for_false_without_any_delegation(session):
    assert not await repository.is_active_deputy_for(
        session, deputy_principal_id="bob", delegator_principal_id="alice"
    )


async def test_is_active_deputy_for_respects_process_definition_scope(session):
    await _create_delegation(
        session, delegator="alice", deputy="bob", scope_process_definition_ids=[7]
    )

    assert await repository.is_active_deputy_for(
        session,
        deputy_principal_id="bob",
        delegator_principal_id="alice",
        process_definition_id=7,
    )
    assert not await repository.is_active_deputy_for(
        session,
        deputy_principal_id="bob",
        delegator_principal_id="alice",
        process_definition_id=9,
    )
    # Ohne mitgelieferte process_definition_id gilt eine gesetzte Scope-Liste
    # als NICHT erfüllt (fail closed), siehe repository._delegation_scope_matches.
    assert not await repository.is_active_deputy_for(
        session, deputy_principal_id="bob", delegator_principal_id="alice"
    )


async def test_is_active_deputy_for_respects_folder_and_object_type_scope(session):
    await _create_delegation(
        session,
        delegator="alice",
        deputy="bob",
        scope_object_type_ids=[3],
        scope_folder_resource_ids=["folder-a"],
    )

    assert await repository.is_active_deputy_for(
        session,
        deputy_principal_id="bob",
        delegator_principal_id="alice",
        object_type_id=3,
        folder_resource_id="folder-a",
    )
    assert not await repository.is_active_deputy_for(
        session,
        deputy_principal_id="bob",
        delegator_principal_id="alice",
        object_type_id=3,
        folder_resource_id="folder-b",
    )
