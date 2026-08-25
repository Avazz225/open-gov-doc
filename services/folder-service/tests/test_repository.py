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


async def test_delete_folder_removes_hand_folder_references(session):
    """Regression (14.2, post-roadmap phase 31 session 7, ADR 0118) - found
    live during this session's own verification: an active reference
    otherwise violates `folder_document_reference`'s FK on
    `folder.folder.id` (`IntegrityError`) once `delete_folder` tries to
    remove the folder row itself."""
    folder = await _make_folder(session)
    await repository.add_document_reference(
        session, folder.id, document_id="doc-1", added_by="alice"
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


async def _make_folder(session, *, name="Handakte-Test"):
    return await repository.create_folder(
        session,
        name=name,
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )


async def test_add_and_list_document_reference(session):
    """Hand folders (14.2, post-roadmap phase 31 session 7, ADR 0118)."""
    folder = await _make_folder(session)
    reference = await repository.add_document_reference(
        session, folder.id, document_id="doc-1", added_by="alice"
    )
    assert reference.folder_id == folder.id
    assert reference.document_id == "doc-1"
    assert reference.removed_at is None

    references = await repository.list_document_references(session, folder.id)
    assert [r.document_id for r in references] == ["doc-1"]


async def test_add_document_reference_unknown_folder_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.add_document_reference(
            session, "does-not-exist", document_id="doc-1", added_by="alice"
        )


async def test_add_document_reference_allows_duplicates(session):
    """Same permissive precedent as case-service's `CaseDocumentReference` -
    no uniqueness constraint, referencing the same document twice is
    harmless and not worth a new constraint this feature didn't ask for."""
    folder = await _make_folder(session)
    await repository.add_document_reference(
        session, folder.id, document_id="doc-1", added_by="alice"
    )
    await repository.add_document_reference(session, folder.id, document_id="doc-1", added_by="bob")
    references = await repository.list_document_references(session, folder.id)
    assert len(references) == 2


async def test_remove_document_reference_marks_removed_not_deleted(session):
    folder = await _make_folder(session)
    await repository.add_document_reference(
        session, folder.id, document_id="doc-1", added_by="alice"
    )
    removed = await repository.remove_document_reference(
        session, folder.id, "doc-1", removed_by="bob"
    )
    assert removed.removed_by == "bob"
    assert removed.removed_at is not None

    # Soft-removed, for traceability - stays in the list (mirrors
    # case-service's `list_document_references`), just with removed_at set.
    references = await repository.list_document_references(session, folder.id)
    assert len(references) == 1
    assert references[0].removed_at is not None


async def test_remove_unknown_document_reference_raises(session):
    folder = await _make_folder(session)
    with pytest.raises(repository.NotFoundError):
        await repository.remove_document_reference(session, folder.id, "doc-1", removed_by="bob")


async def test_remove_already_removed_document_reference_raises(session):
    folder = await _make_folder(session)
    await repository.add_document_reference(
        session, folder.id, document_id="doc-1", added_by="alice"
    )
    await repository.remove_document_reference(session, folder.id, "doc-1", removed_by="bob")
    with pytest.raises(repository.NotFoundError):
        await repository.remove_document_reference(session, folder.id, "doc-1", removed_by="carol")
