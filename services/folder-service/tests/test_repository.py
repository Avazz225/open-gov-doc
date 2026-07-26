import pytest
from folder_service import repository
from folder_service.settings import ROOT_FOLDER_ID


async def test_root_folder_exists(session):
    root = await repository.get_folder(session, ROOT_FOLDER_ID)
    assert root.parent_id is None


async def test_create_folder_under_root(session):
    folder = await repository.create_folder(
        session,
        name="Projekte",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    assert folder.parent_id == ROOT_FOLDER_ID

    children = await repository.list_children(session, ROOT_FOLDER_ID)
    assert folder.id in {c.id for c in children}


async def test_create_folder_unknown_parent_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.create_folder(
            session,
            name="X",
            parent_id="does-not-exist",
            object_type_id=None,
            attributes={},
            created_by="alice",
        )


async def test_update_rename(session):
    folder = await repository.create_folder(
        session,
        name="Alt",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    updated, moved = await repository.update_folder(
        session, folder.id, name="Neu", new_parent_id=None, attributes=None
    )
    assert updated.name == "Neu"
    assert moved is False


async def test_update_move_reports_moved(session):
    parent_a = await repository.create_folder(
        session,
        name="A",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    parent_b = await repository.create_folder(
        session,
        name="B",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    child = await repository.create_folder(
        session,
        name="Kind",
        parent_id=parent_a.id,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )

    updated, moved = await repository.update_folder(
        session, child.id, name=None, new_parent_id=parent_b.id, attributes=None
    )
    assert moved is True
    assert updated.parent_id == parent_b.id


async def test_update_move_to_self_raises(session):
    folder = await repository.create_folder(
        session,
        name="X",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    with pytest.raises(ValueError):
        await repository.update_folder(
            session, folder.id, name=None, new_parent_id=folder.id, attributes=None
        )


async def test_delete_empty_folder(session):
    folder = await repository.create_folder(
        session,
        name="Leer",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await repository.delete_folder(session, folder.id)
    with pytest.raises(repository.NotFoundError):
        await repository.get_folder(session, folder.id)


async def test_delete_non_empty_folder_raises(session):
    parent = await repository.create_folder(
        session,
        name="Parent",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await repository.create_folder(
        session,
        name="Child",
        parent_id=parent.id,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    with pytest.raises(repository.FolderNotEmptyError):
        await repository.delete_folder(session, parent.id)
