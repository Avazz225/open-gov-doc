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
