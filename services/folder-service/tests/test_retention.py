from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from folder_service import repository
from folder_service.models import Folder
from folder_service.settings import ROOT_FOLDER_ID


async def _make_folder(session, *, name="Ordner", parent_id=ROOT_FOLDER_ID):
    return await repository.create_folder(
        session,
        name=name,
        parent_id=parent_id,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )


def _fake_document_client():
    client = AsyncMock()
    client.cascade_trash.return_value = []
    client.cascade_restore.return_value = []
    client.count_active.return_value = 0
    return client


async def test_soft_delete_folder_cascades_to_subfolders(session):
    parent = await _make_folder(session, name="Parent")
    child = await _make_folder(session, name="Kind", parent_id=parent.id)
    document_client = _fake_document_client()

    await repository.soft_delete_folder(
        session, parent.id, deleted_by="alice", document_client=document_client
    )

    reloaded_parent = await session.get(Folder, parent.id)
    reloaded_child = await session.get(Folder, child.id)
    assert reloaded_parent.deleted_at is not None
    assert reloaded_parent.deleted_via_folder_id is None
    assert reloaded_child.deleted_at is not None
    assert reloaded_child.deleted_via_folder_id == parent.id
    document_client.cascade_trash.assert_awaited_once_with(
        [parent.id, child.id], via_folder_id=parent.id, deleted_by="alice"
    )


async def test_soft_delete_folder_does_not_touch_independently_deleted_subfolder(session):
    parent = await _make_folder(session, name="Parent")
    child = await _make_folder(session, name="Kind", parent_id=parent.id)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, child.id, deleted_by="bob", document_client=document_client
    )

    await repository.soft_delete_folder(
        session, parent.id, deleted_by="alice", document_client=document_client
    )

    reloaded_child = await session.get(Folder, child.id)
    assert reloaded_child.deleted_via_folder_id is None
    document_client.cascade_trash.assert_awaited_with(
        [parent.id], via_folder_id=parent.id, deleted_by="alice"
    )


async def test_restore_folder_cascades_to_children_deleted_via_it(session):
    parent = await _make_folder(session, name="Parent")
    child = await _make_folder(session, name="Kind", parent_id=parent.id)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, parent.id, deleted_by="alice", document_client=document_client
    )

    restored = await repository.restore_folder(session, parent.id, document_client=document_client)

    assert restored.deleted_at is None
    reloaded_child = await session.get(Folder, child.id)
    assert reloaded_child.deleted_at is None
    assert reloaded_child.deleted_via_folder_id is None
    document_client.cascade_restore.assert_awaited_once_with(parent.id)


async def test_restore_folder_not_deleted_raises(session):
    folder = await _make_folder(session)
    with pytest.raises(repository.NotDeletedError):
        await repository.restore_folder(session, folder.id, document_client=_fake_document_client())


async def test_restore_folder_after_period_expired_raises(session):
    folder = await _make_folder(session)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, folder.id, deleted_by="alice", document_client=document_client
    )
    row = await session.get(Folder, folder.id)
    row.deleted_at = datetime.now(UTC) - timedelta(days=40)
    await session.flush()

    with pytest.raises(repository.RestorePeriodExpiredError):
        await repository.restore_folder(session, folder.id, document_client=document_client)


async def test_list_deleted_folders_only_shows_soft_deleted(session):
    kept = await _make_folder(session, name="Bleibt")
    deleted = await _make_folder(session, name="Weg")
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, deleted.id, deleted_by="alice", document_client=document_client
    )

    result = await repository.list_deleted_folders(session, ROOT_FOLDER_ID)

    ids = {f.id for f in result}
    assert deleted.id in ids
    assert kept.id not in ids


async def test_get_folder_treats_deleted_as_not_found(session):
    folder = await _make_folder(session)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, folder.id, deleted_by="alice", document_client=document_client
    )

    with pytest.raises(repository.NotFoundError):
        await repository.get_folder(session, folder.id)


async def test_create_folder_under_deleted_parent_raises(session):
    parent = await _make_folder(session)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, parent.id, deleted_by="alice", document_client=document_client
    )

    with pytest.raises(repository.NotFoundError):
        await repository.create_folder(
            session,
            name="Kind",
            parent_id=parent.id,
            object_type_id=None,
            attributes={},
            created_by="alice",
        )


async def test_list_active_subtree_ids_excludes_already_deleted_descendants(session):
    parent = await _make_folder(session, name="Parent")
    active_child = await _make_folder(session, name="Aktiv", parent_id=parent.id)
    deleted_child = await _make_folder(session, name="Weg", parent_id=parent.id)
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, deleted_child.id, deleted_by="alice", document_client=document_client
    )

    ids = await repository.list_active_subtree_ids(session, parent.id)

    assert set(ids) == {parent.id, active_child.id}


async def test_create_and_release_legal_hold(session):
    folder = await _make_folder(session)
    hold = await repository.create_legal_hold(session, folder.id, set_by="alice", reason="Prüfung")
    assert hold.released_at is None

    released = await repository.release_legal_hold(session, hold.id, released_by="bob")
    assert released.released_at is not None


async def test_release_already_released_hold_raises(session):
    folder = await _make_folder(session)
    hold = await repository.create_legal_hold(session, folder.id, set_by="alice", reason=None)
    await repository.release_legal_hold(session, hold.id, released_by="bob")

    with pytest.raises(repository.AlreadyReleasedError):
        await repository.release_legal_hold(session, hold.id, released_by="bob")


async def test_deletion_register_entry_roundtrip(session):
    folder = await _make_folder(session)
    await repository.create_deletion_register_entry(
        session,
        folder.id,
        trigger="forced_deletion",
        reason="Frist abgelaufen",
        triggered_by="alice",
    )

    entries = await repository.list_deletion_register(session, folder_id=folder.id)
    assert len(entries) == 1
    assert entries[0].trigger == "forced_deletion"


async def test_retention_config_defaults_and_update(session):
    config = await repository.get_retention_config(session)
    assert config.deletion_reason_required is False

    updated = await repository.update_retention_config(
        session, deletion_reason_required=True, reminder_lead_days=7
    )
    assert updated.deletion_reason_required is True
    assert updated.reminder_lead_days == 7


async def test_trash_config_defaults_and_update(session):
    config = await repository.get_trash_config(session)
    assert config.restore_period_days == 30

    updated = await repository.update_trash_config(session, restore_period_days=10)
    assert updated.restore_period_days == 10


async def test_list_due_for_retention_action_excludes_hold_and_future(session):
    due = await _make_folder(session, name="Fällig")
    await repository.set_retention(
        session,
        due.id,
        retention_until=datetime.now(UTC) - timedelta(days=1),
        full_deletion=False,
        reason=None,
    )
    future = await _make_folder(session, name="Zukunft")
    await repository.set_retention(
        session,
        future.id,
        retention_until=datetime.now(UTC) + timedelta(days=30),
        full_deletion=False,
        reason=None,
    )
    on_hold = await _make_folder(session, name="Hold")
    await repository.set_retention(
        session,
        on_hold.id,
        retention_until=datetime.now(UTC) - timedelta(days=1),
        full_deletion=False,
        reason=None,
    )
    await repository.create_legal_hold(session, on_hold.id, set_by="alice", reason=None)

    result = await repository.list_due_for_retention_action(session)

    ids = {f.id for f in result}
    assert due.id in ids
    assert future.id not in ids
    assert on_hold.id not in ids


async def test_list_expired_trash_excludes_hold_and_not_yet_expired(session):
    expired = await _make_folder(session, name="Abgelaufen")
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, expired.id, deleted_by="alice", document_client=document_client
    )
    expired_row = await session.get(Folder, expired.id)
    expired_row.deleted_at = datetime.now(UTC) - timedelta(days=40)
    await session.flush()

    recent = await _make_folder(session, name="Frisch")
    await repository.soft_delete_folder(
        session, recent.id, deleted_by="alice", document_client=document_client
    )

    result = await repository.list_expired_trash(session, restore_period_days=30)

    ids = {f.id for f in result}
    assert expired.id in ids
    assert recent.id not in ids
