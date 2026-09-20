"""P55-S1/ADR 0175: `consumer.py`'s `folder.resource.deleted` handler
tested directly (`make_handler`'s returned `handle()` called with a real
`Event`, no real NATS round-trip needed) - same pattern as
`case-service/tests/test_consumer.py`, a `FakePermissionClient` records
calls instead of hitting the real, live-running `permission-service`."""

from dms_db_base import make_session_factory
from dms_eventbus_client import Event
from teamspace_service import consumer, repository


class FakePermissionClient:
    """Ersetzt den echten HTTP-Client (kein permission-service-Aufruf noetig,
    gleiches Fake-Muster wie case-service/tests/test_consumer.py)."""

    def __init__(self) -> None:
        self.revoked_resource: list[tuple[str, str]] = []
        self.revoked_manager: list[tuple[str, str]] = []

    async def revoke_resource_access(self, *, principal_id: str, resource_id: str) -> None:
        self.revoked_resource.append((principal_id, resource_id))

    async def revoke_manager_access(self, *, principal_id: str, resource_id: str) -> None:
        self.revoked_manager.append((principal_id, resource_id))


def _session_factory(engine):
    return make_session_factory(engine)


async def test_folder_resource_deleted_tears_down_the_matching_teamspace(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        teamspace = await repository.create_teamspace(
            session,
            name="Projekt X",
            description="",
            root_folder_id="folder-gone",
            created_by="alice",
        )
        await repository.add_member(
            session, teamspace.id, principal_id="bob", can_manage_members=False, invited_by="alice"
        )
        await session.commit()
        teamspace_id = teamspace.id

    fake_permission = FakePermissionClient()
    handler = consumer.make_handler(session_factory, fake_permission)
    event = Event(
        event_type="folder.resource.deleted",
        service_name="folder-service",
        subject="folder-gone",
        payload={"resource_id": "folder-gone"},
    )
    await handler(event.to_bytes())

    async with session_factory() as session:
        result = await repository.get_teamspace_by_root_folder_id(session, "folder-gone")
        assert result is None
        assert await repository.list_members(session, teamspace_id) == []

    assert set(fake_permission.revoked_resource) == {
        ("alice", "folder-gone"),
        ("bob", "folder-gone"),
    }
    assert set(fake_permission.revoked_manager) == {
        ("alice", "folder-gone"),
        ("bob", "folder-gone"),
    }


async def test_folder_resource_deleted_for_an_unrelated_folder_is_a_no_op(engine):
    session_factory = _session_factory(engine)
    async with session_factory() as session:
        await repository.create_teamspace(
            session,
            name="Projekt X",
            description="",
            root_folder_id="folder-still-here",
            created_by="alice",
        )
        await session.commit()

    fake_permission = FakePermissionClient()
    handler = consumer.make_handler(session_factory, fake_permission)
    event = Event(
        event_type="folder.resource.deleted",
        service_name="folder-service",
        subject="some-other-folder",
        payload={"resource_id": "some-other-folder"},
    )
    await handler(event.to_bytes())

    async with session_factory() as session:
        result = await repository.get_teamspace_by_root_folder_id(session, "folder-still-here")
        assert result is not None
    assert fake_permission.revoked_resource == []
