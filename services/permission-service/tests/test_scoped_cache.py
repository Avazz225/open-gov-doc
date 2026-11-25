"""Scoped cache invalidation and batch computation (P95-S1d, ADR 0263).

- A change that can only affect certain principals invalidates just them; the
  rest of the cache stays warm.
- A group's members and its assignments change under one lock, so a member
  added during an assignment change can't keep a stale entry.
- A batch computes siblings together and gives exactly what the single
  computation gives.
"""

import asyncio

import pytest
from dms_db_base import make_session_factory
from permission_service import repository
from permission_service.models import EffectivePermissionCache, ResourceNode
from permission_service.settings import ROOT_RESOURCE_ID
from sqlalchemy import event, text


async def _cached(session, principal_id, resource_id=ROOT_RESOURCE_ID):
    """The stored row, or None - what a later lookup could hit."""
    return await session.get(
        EffectivePermissionCache, (principal_id, resource_id), populate_existing=True
    )


async def _is_hit(session, principal_id, resource_id=ROOT_RESOURCE_ID) -> bool:
    row = await _cached(session, principal_id, resource_id)
    if row is None:
        return False
    generation, principal_generation = await repository._cache_generations(session, principal_id)
    return row.generation == generation and row.principal_generation == principal_generation


async def _grant(session, principal_type, principal_id, role, resource_id=ROOT_RESOURCE_ID):
    return await repository.create_role_assignment(
        session,
        principal_type=principal_type,
        principal_id=principal_id,
        role_id=role.id,
        resource_id=resource_id,
    )


async def test_a_user_assignment_leaves_other_principals_warm(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    for principal in ("alice", "bob"):
        await repository.get_effective_permissions(session, principal, ROOT_RESOURCE_ID)
    global_before = await repository.current_cache_generation(session)

    await _grant(session, "user", "alice", role)

    assert await repository.current_cache_generation(session) == global_before
    assert await _is_hit(session, "bob")
    assert not await _is_hit(session, "alice")
    entry = await repository.get_effective_permissions(session, "alice", ROOT_RESOURCE_ID)
    assert entry.permissions == ["read"]


async def test_a_group_assignment_reaches_exactly_its_members(session):
    role = await repository.create_role(session, "Editor", "", ["write"])
    group = await repository.create_group(session, "Redaktion", "")
    await repository.add_group_member(session, group.id, "member")
    for principal in ("member", "outsider"):
        await repository.get_effective_permissions(session, principal, ROOT_RESOURCE_ID)

    assignment = await _grant(session, "group", group.id, role)

    assert await _is_hit(session, "outsider")
    assert not await _is_hit(session, "member")
    member = await repository.get_effective_permissions(session, "member", ROOT_RESOURCE_ID)
    assert member.permissions == ["write"]

    await repository.delete_role_assignment(session, assignment.id)
    assert await _is_hit(session, "outsider")
    member = await repository.get_effective_permissions(session, "member", ROOT_RESOURCE_ID)
    assert member.permissions == []


async def test_membership_changes_touch_only_that_member(session):
    role = await repository.create_role(session, "Editor", "", ["write"])
    group = await repository.create_group(session, "Redaktion", "")
    await _grant(session, "group", group.id, role)
    await repository.add_group_member(session, group.id, "old-member")
    for principal in ("old-member", "newcomer"):
        await repository.get_effective_permissions(session, principal, ROOT_RESOURCE_ID)

    await repository.add_group_member(session, group.id, "newcomer")
    assert await _is_hit(session, "old-member")
    newcomer = await repository.get_effective_permissions(session, "newcomer", ROOT_RESOURCE_ID)
    assert newcomer.permissions == ["write"]

    await repository.remove_group_member(session, group.id, "newcomer")
    assert await _is_hit(session, "old-member")
    newcomer = await repository.get_effective_permissions(session, "newcomer", ROOT_RESOURCE_ID)
    assert newcomer.permissions == []

    # Deleting the group takes the right from its remaining members only.
    await repository.get_effective_permissions(session, "newcomer", ROOT_RESOURCE_ID)
    await repository.delete_group(session, group.id)
    assert await _is_hit(session, "newcomer")
    old = await repository.get_effective_permissions(session, "old-member", ROOT_RESOURCE_ID)
    assert old.permissions == []


async def test_everyone_and_role_changes_stay_global(session):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await repository.get_effective_permissions(session, "carla", ROOT_RESOURCE_ID)
    before = await repository.current_cache_generation(session)

    await _grant(
        session, repository.EVERYONE_PRINCIPAL_TYPE, repository.EVERYONE_PRINCIPAL_ID, role
    )
    assert await repository.current_cache_generation(session) == before + 1
    carla = await repository.get_effective_permissions(session, "carla", ROOT_RESOURCE_ID)
    assert carla.permissions == ["read"]

    await repository.update_role(session, role.id, description="", permissions=["read", "comment"])
    assert await repository.current_cache_generation(session) == before + 2


async def test_a_group_assignment_change_holds_the_group_against_new_members(engine):
    """The interleaving the group lock exists for: while an assignment change
    to a group is uncommitted, adding a member waits - so the member's own
    invalidation comes after, and nothing can cache the group's old rights
    for the new member under a generation that stays current."""
    factory = make_session_factory(engine)
    async with factory() as setup:
        await repository.ensure_root_resource(setup)
        role = await repository.create_role(setup, "Lock", "", ["lock.read"])
        group = await repository.create_group(setup, "Sperrgruppe", "")
        await setup.commit()

    async with factory() as admin:
        await _grant(admin, "group", group.id, role)  # holds the group row lock
        async with factory() as other:
            await other.execute(text("SET LOCAL lock_timeout = '300ms'"))
            with pytest.raises(Exception, match="lock timeout"):
                await repository.add_group_member(other, group.id, "late-member")
            await other.rollback()
        await admin.commit()

    async with factory() as other:
        await repository.add_group_member(other, group.id, "late-member")
        await other.commit()
    async with factory() as reader:
        entry = await repository.get_effective_permissions(reader, "late-member", ROOT_RESOURCE_ID)
        assert entry.permissions == ["lock.read"]


async def test_a_revoke_racing_a_recompute_is_not_undone_per_principal(engine, monkeypatch):
    """ADR 0239's race, on the per-principal generation alone (the global one
    doesn't move): a recompute paused after collecting the roles while the
    revoke commits must not leave a row that later counts as a hit."""
    factory = make_session_factory(engine)
    async with factory() as setup:
        await repository.ensure_root_resource(setup)
        role = await repository.create_role(setup, "Race", "", ["race.p"])
        assignment = await _grant(setup, "user", "racer", role)
        await setup.commit()
        global_before = await repository.current_cache_generation(setup)

    gate, started = asyncio.Event(), asyncio.Event()
    real_collect = repository._collect_effective_roles

    async def collect_then_wait(session, pid, rid):
        result = await real_collect(session, pid, rid)
        started.set()
        await gate.wait()
        return result

    monkeypatch.setattr(repository, "_collect_effective_roles", collect_then_wait)

    async def recompute():
        async with factory() as s:
            entry = await repository.get_effective_permissions(s, "racer", ROOT_RESOURCE_ID)
            await s.commit()
            return entry

    reader = asyncio.create_task(recompute())
    await started.wait()
    async with factory() as writer:
        await repository.delete_role_assignment(writer, assignment.id)
        await writer.commit()
    gate.set()
    assert "race.p" in (await reader).permissions  # what the paused request saw
    monkeypatch.setattr(repository, "_collect_effective_roles", real_collect)

    async with factory() as s:
        assert await repository.current_cache_generation(s) == global_before
        assert not await _is_hit(s, "racer")
        entry = await repository.get_effective_permissions(s, "racer", ROOT_RESOURCE_ID)
        assert entry.permissions == []


async def _tree(session):
    """root - abteilung - akte (inherit=False) - 300 children, plus a branch
    under abteilung that inherits normally."""
    for node_id, parent, inherit in (
        ("abteilung", ROOT_RESOURCE_ID, True),
        ("akte", "abteilung", False),
        ("offen", "abteilung", True),
    ):
        session.add(ResourceNode(resource_id=node_id, parent_id=parent, inherit=inherit))
    children = [f"kind-{n:03d}" for n in range(300)]
    for child in children:
        session.add(ResourceNode(resource_id=child, parent_id="akte", inherit=True))
    for n in range(5):
        session.add(ResourceNode(resource_id=f"offen-{n}", parent_id="offen", inherit=True))
    await session.flush()
    return children


async def test_batch_equals_single_computation(session):
    children = await _tree(session)
    viewer = await repository.create_role(session, "Viewer", "", ["read"])
    editor = await repository.create_role(session, "Editor", "", ["write"])
    special = await repository.create_role(session, "Special", "", ["special"])
    group = await repository.create_group(session, "Team", "")
    await repository.add_group_member(session, group.id, "dora")
    await _grant(session, "user", "dora", viewer, ROOT_RESOURCE_ID)  # cut off at "akte"
    await _grant(session, "group", group.id, editor, "akte")
    await _grant(session, "user", "dora", special, "kind-007")
    await _grant(
        session,
        repository.EVERYONE_PRINCIPAL_TYPE,
        repository.EVERYONE_PRINCIPAL_ID,
        viewer,
        "abteilung",
    )
    ids = [*children, "offen-0", "offen-4", "akte", "abteilung", "not-registered"]

    batch = await repository.get_effective_permissions_batch(session, "dora", ids)
    await repository.invalidate_principals(session, {"dora"})
    for resource_id in ids:
        single = await repository.get_effective_permissions(session, "dora", resource_id)
        assert (batch[resource_id].roles, batch[resource_id].permissions) == (
            single.roles,
            single.permissions,
        ), resource_id
    assert batch["kind-007"].permissions == ["special", "write"]
    assert batch["kind-001"].permissions == ["write"]
    assert batch["offen-4"].permissions == ["read"]
    assert batch["not-registered"].permissions == []
    assert await _cached(session, "dora", "not-registered") is None


async def test_a_cold_batch_walks_shared_ancestors_once(engine, session):
    children = await _tree(session)
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await _grant(session, "user", "erik", role, "akte")
    await session.commit()

    statements = []

    def on_execute(*_args):
        statements.append(1)

    event.listen(engine.sync_engine, "before_cursor_execute", on_execute)
    try:
        cold = await repository.get_effective_permissions_batch(session, "erik", children)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", on_execute)
    assert all(entry.permissions == ["read"] for entry in cold.values())
    # Generations, the cached rows, groups, then per tree level nodes and
    # assignments, the roles and one upsert - not one walk per child.
    assert len(statements) <= 10, len(statements)

    warm = await repository.get_effective_permissions_batch(session, "erik", children)
    assert [warm[c].permissions for c in children] == [["read"]] * len(children)


# --- The hot path's single read (P95-S2b) -----------------------------------


async def test_the_cached_lookup_returns_only_entries_valid_in_both_generations(session, engine):
    role = await repository.create_role(session, "Viewer", "", ["read"])
    await _grant(session, "user", "fritz", role)
    await repository.get_effective_permissions(session, "fritz", ROOT_RESOURCE_ID)
    await session.commit()

    lookup = await repository.cached_effective_permissions(engine, "fritz", [ROOT_RESOURCE_ID])
    assert lookup.entries[ROOT_RESOURCE_ID].permissions == ["read"]
    assert lookup.any_lock_active is False

    # A change to fritz moves his generation: the row is no longer a hit.
    await repository.invalidate_principals(session, {"fritz"})
    await session.commit()
    lookup = await repository.cached_effective_permissions(engine, "fritz", [ROOT_RESOURCE_ID])
    assert lookup.entries == {}
    # And a global change does the same for everyone.
    await repository.get_effective_permissions(session, "fritz", ROOT_RESOURCE_ID)
    await repository.invalidate_cache(session)
    await session.commit()
    assert (
        await repository.cached_effective_permissions(engine, "fritz", [ROOT_RESOURCE_ID])
    ).entries == {}


async def test_the_cached_lookup_knows_whether_any_scope_lock_is_active(session, engine):
    lock = await repository.create_scope_lock(
        session,
        resource_id=ROOT_RESOURCE_ID,
        locked_by="admin",
        reason="Test",
        blocks_read=False,
        expires_at=None,
    )
    await session.commit()
    assert (await repository.cached_effective_permissions(engine, "x", ["a"])).any_lock_active
    await repository.release_scope_lock(session, lock.id, "admin")
    await session.commit()
    assert not (await repository.cached_effective_permissions(engine, "x", ["a"])).any_lock_active
