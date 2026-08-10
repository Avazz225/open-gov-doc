from datetime import UTC, datetime, timedelta

import pytest
from teamspace_service import repository


async def test_create_teamspace_creates_teamspace_and_creator_member(session):
    teamspace = await repository.create_teamspace(
        session,
        name="Projekt X",
        description="Testbeschreibung",
        root_folder_id="folder-1",
        created_by="alice",
    )
    assert teamspace.name == "Projekt X"
    assert teamspace.root_folder_id == "folder-1"

    member = await repository.get_member(session, teamspace.id, "alice")
    assert member is not None
    assert member.can_manage_members is True
    assert member.invited_by == "alice"


async def test_get_teamspace_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_teamspace(session, "does-not-exist")


async def test_list_teamspaces_for_principal_only_shows_member_of(session):
    ts1 = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.create_teamspace(
        session, name="B", description="", root_folder_id="f2", created_by="bob"
    )
    alice_teamspaces = await repository.list_teamspaces_for_principal(session, "alice")
    assert [t.id for t in alice_teamspaces] == [ts1.id]


async def test_delete_teamspace_removes_members_appointments_contacts(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.create_appointment(
        session,
        teamspace.id,
        title="Kickoff",
        description="",
        start_at=datetime.now(UTC),
        end_at=datetime.now(UTC) + timedelta(hours=1),
        created_by="alice",
    )
    await repository.create_contact(
        session,
        teamspace.id,
        name="Externe Kontaktperson",
        email=None,
        phone=None,
        note="",
        created_by="alice",
    )
    await repository.delete_teamspace(session, teamspace.id)

    with pytest.raises(repository.NotFoundError):
        await repository.get_teamspace(session, teamspace.id)
    assert await repository.list_members(session, teamspace.id) == []
    assert await repository.list_appointments(session, teamspace.id) == []
    assert await repository.list_contacts(session, teamspace.id) == []


async def test_get_member_returns_none_when_not_a_member(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    assert await repository.get_member(session, teamspace.id, "bob") is None


async def test_add_member_succeeds(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    member = await repository.add_member(
        session, teamspace.id, principal_id="bob", can_manage_members=False, invited_by="alice"
    )
    assert member.principal_id == "bob"
    assert member.can_manage_members is False


async def test_add_member_duplicate_raises(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    with pytest.raises(repository.DuplicateMemberError):
        await repository.add_member(
            session,
            teamspace.id,
            principal_id="alice",
            can_manage_members=False,
            invited_by="alice",
        )


async def test_update_member_changes_can_manage_members(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.add_member(
        session, teamspace.id, principal_id="bob", can_manage_members=False, invited_by="alice"
    )
    updated = await repository.update_member(session, teamspace.id, "bob", can_manage_members=True)
    assert updated.can_manage_members is True


async def test_update_member_unknown_raises(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    with pytest.raises(repository.NotFoundError):
        await repository.update_member(session, teamspace.id, "ghost", can_manage_members=True)


async def test_remove_member_succeeds(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.add_member(
        session, teamspace.id, principal_id="bob", can_manage_members=False, invited_by="alice"
    )
    await repository.remove_member(session, teamspace.id, "bob")
    assert await repository.get_member(session, teamspace.id, "bob") is None


async def test_remove_member_unknown_raises(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    with pytest.raises(repository.NotFoundError):
        await repository.remove_member(session, teamspace.id, "ghost")


async def test_list_members_ordered_by_invited_at(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.add_member(
        session, teamspace.id, principal_id="bob", can_manage_members=False, invited_by="alice"
    )
    members = await repository.list_members(session, teamspace.id)
    assert [m.principal_id for m in members] == ["alice", "bob"]


async def test_create_appointment_and_list_ordered_by_start_at(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    now = datetime.now(UTC)
    later = await repository.create_appointment(
        session,
        teamspace.id,
        title="Später",
        description="",
        start_at=now + timedelta(days=2),
        end_at=now + timedelta(days=2, hours=1),
        created_by="alice",
    )
    earlier = await repository.create_appointment(
        session,
        teamspace.id,
        title="Früher",
        description="",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=1, hours=1),
        created_by="alice",
    )
    appointments = await repository.list_appointments(session, teamspace.id)
    assert [a.id for a in appointments] == [earlier.id, later.id]


async def test_delete_appointment_succeeds(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    now = datetime.now(UTC)
    appointment = await repository.create_appointment(
        session,
        teamspace.id,
        title="Kickoff",
        description="",
        start_at=now,
        end_at=now + timedelta(hours=1),
        created_by="alice",
    )
    await repository.delete_appointment(session, teamspace.id, appointment.id)
    assert await repository.list_appointments(session, teamspace.id) == []


async def test_delete_appointment_wrong_teamspace_raises(session):
    ts1 = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    ts2 = await repository.create_teamspace(
        session, name="B", description="", root_folder_id="f2", created_by="alice"
    )
    now = datetime.now(UTC)
    appointment = await repository.create_appointment(
        session,
        ts1.id,
        title="Kickoff",
        description="",
        start_at=now,
        end_at=now + timedelta(hours=1),
        created_by="alice",
    )
    with pytest.raises(repository.NotFoundError):
        await repository.delete_appointment(session, ts2.id, appointment.id)


async def test_create_contact_and_list_ordered_by_name(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    await repository.create_contact(
        session,
        teamspace.id,
        name="Zeta GmbH",
        email=None,
        phone=None,
        note="",
        created_by="alice",
    )
    await repository.create_contact(
        session,
        teamspace.id,
        name="Anna Beispiel",
        email="anna@example.com",
        phone="123",
        note="Externe Ansprechperson",
        created_by="alice",
    )
    contacts = await repository.list_contacts(session, teamspace.id)
    assert [c.name for c in contacts] == ["Anna Beispiel", "Zeta GmbH"]


async def test_delete_contact_succeeds(session):
    teamspace = await repository.create_teamspace(
        session, name="A", description="", root_folder_id="f1", created_by="alice"
    )
    contact = await repository.create_contact(
        session, teamspace.id, name="Anna", email=None, phone=None, note="", created_by="alice"
    )
    await repository.delete_contact(session, teamspace.id, contact.id)
    assert await repository.list_contacts(session, teamspace.id) == []
