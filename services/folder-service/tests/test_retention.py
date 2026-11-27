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

    restored = await repository.restore_folder(
        session, parent.id, document_client=document_client, restored_by="petra-restorer"
    )

    assert restored.deleted_at is None
    reloaded_child = await session.get(Folder, child.id)
    assert reloaded_child.deleted_at is None
    assert reloaded_child.deleted_via_folder_id is None
    document_client.cascade_restore.assert_awaited_once_with(
        parent.id, restored_by="petra-restorer"
    )


async def test_restore_folder_not_deleted_raises(session):
    folder = await _make_folder(session)
    with pytest.raises(repository.NotDeletedError):
        await repository.restore_folder(
            session,
            folder.id,
            document_client=_fake_document_client(),
            restored_by="petra-restorer",
        )


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
        await repository.restore_folder(
            session, folder.id, document_client=document_client, restored_by="petra-restorer"
        )


async def test_soft_delete_folder_persists_deleted_by(session):
    """P15-S0-Fund: `deleted_by` wurde bislang entgegengenommen, aber nie
    tatsächlich gespeichert - Voraussetzung für den persönlichen Papierkorb
    (2.5, P15-S1)."""
    folder = await _make_folder(session, name="Weg")
    document_client = _fake_document_client()
    deleted = await repository.soft_delete_folder(
        session, folder.id, deleted_by="alice", document_client=document_client
    )
    assert deleted.deleted_by == "alice"


async def test_restore_folder_clears_deleted_by(session):
    folder = await _make_folder(session, name="Weg")
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, folder.id, deleted_by="alice", document_client=document_client
    )
    restored = await repository.restore_folder(
        session, folder.id, document_client=document_client, restored_by="petra-restorer"
    )
    assert restored.deleted_by is None


async def test_list_deleted_folders_filters_by_deleted_by(session):
    own = await _make_folder(session, name="Eigener")
    other = await _make_folder(session, name="Fremder")
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, own.id, deleted_by="alice", document_client=document_client
    )
    await repository.soft_delete_folder(
        session, other.id, deleted_by="bob", document_client=document_client
    )

    result = await repository.list_deleted_folders(session, deleted_by="alice")

    ids = {f.id for f in result}
    assert own.id in ids
    assert other.id not in ids


async def test_list_deleted_folders_only_shows_soft_deleted(session):
    kept = await _make_folder(session, name="Bleibt")
    deleted = await _make_folder(session, name="Weg")
    document_client = _fake_document_client()
    await repository.soft_delete_folder(
        session, deleted.id, deleted_by="alice", document_client=document_client
    )

    result = await repository.list_deleted_folders(session, parent_id=ROOT_FOLDER_ID)

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
    assert config.deletion_reason_catalog == []

    updated = await repository.update_retention_config(
        session,
        deletion_reason_required=True,
        reminder_lead_days=7,
        deletion_reason_catalog=["Dublette"],
    )
    assert updated.deletion_reason_required is True
    assert updated.reminder_lead_days == 7
    assert updated.deletion_reason_catalog == ["Dublette"]


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


# --- Trash purge bottom-up (P98-S1, ADR 0268) --------------------------------


async def _trash_subtree(session, root, *, days_ago=40):
    """Trashes `root` (cascading to its subtree) and backdates the whole
    cascade, as the restore period would have run out."""
    from folder_service import retention_actions  # noqa: F401  (import check)

    await repository.soft_delete_folder(
        session, root.id, deleted_by="alice", document_client=_fake_document_client()
    )
    for folder in await repository.list_deleted_folders(session):
        if folder.id == root.id or folder.deleted_via_folder_id == root.id:
            folder.deleted_at = datetime.now(UTC) - timedelta(days=days_ago)
    await session.flush()


async def test_purge_removes_an_expired_subtree_bottom_up(session):
    """Until P98 the parent could come first and hit folder_parent_id_fkey,
    which aborted the whole tick every hour."""
    from folder_service import retention_actions

    parent = await _make_folder(session, name="Akte")
    child = await _make_folder(session, name="Unterakte", parent_id=parent.id)
    grandchild = await _make_folder(session, name="Vorgang", parent_id=child.id)
    await _trash_subtree(session, parent)
    await session.commit()

    result = await retention_actions.purge_expired_trash(session, restore_period_days=30)

    assert set(result.purged) >= {parent.id, child.id, grandchild.id}
    assert result.purged.index(grandchild.id) < result.purged.index(child.id)
    assert result.purged.index(child.id) < result.purged.index(parent.id)
    assert result.deferred == [] and result.failed == []
    for folder_id in (parent.id, child.id, grandchild.id):
        assert await session.get(Folder, folder_id) is None


async def test_purge_defers_a_parent_while_a_held_child_remains(session):
    """A subfolder under legal hold stays; its parent waits for it instead of
    failing - a parent is only due when every child is."""
    from folder_service import retention_actions

    parent = await _make_folder(session, name="Akte mit Sperre")
    held = await _make_folder(session, name="Gesperrt", parent_id=parent.id)
    free = await _make_folder(session, name="Frei", parent_id=parent.id)
    await _trash_subtree(session, parent)
    await repository.create_legal_hold(session, held.id, set_by="alice", reason="Verfahren")
    await session.commit()

    result = await retention_actions.purge_expired_trash(session, restore_period_days=30)

    assert free.id in result.purged
    assert parent.id in result.deferred
    assert held.id not in result.purged
    assert await session.get(Folder, parent.id) is not None
    assert await session.get(Folder, held.id) is not None


async def test_purge_goes_on_after_a_failing_entry(session, monkeypatch):
    from folder_service import retention_actions

    first = await _make_folder(session, name="Kaputt")
    second = await _make_folder(session, name="Heil")
    await _trash_subtree(session, first)
    await _trash_subtree(session, second)
    # Plain ids: the rollback after the failure expires the ORM objects.
    first_id, second_id = first.id, second.id
    await session.commit()
    original = retention_actions.purge_expired_trash_entry

    async def flaky(session_, folder_id, **kwargs):
        if folder_id == first_id:
            raise RuntimeError("boom")
        await original(session_, folder_id, **kwargs)

    monkeypatch.setattr(retention_actions, "purge_expired_trash_entry", flaky)

    result = await retention_actions.purge_expired_trash(session, restore_period_days=30)

    assert first_id in result.failed
    assert second_id in result.purged
