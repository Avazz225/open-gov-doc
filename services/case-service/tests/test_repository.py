import pytest
from case_service import repository


async def _make_case(session, **overrides):
    defaults = dict(
        case_id="case-1",
        name="Umlaufmappe Bauantrag",
        object_type_id=None,
        attributes={},
        process_definition_id=1,
        process_instance_id="instance-1",
        created_by="alice",
    )
    defaults.update(overrides)
    return await repository.create_case(session, **defaults)


async def test_create_and_get_case(session):
    case = await _make_case(session)
    fetched = await repository.get_case(session, case.id)
    assert fetched.id == case.id
    assert fetched.status == "open"
    assert fetched.closed_at is None


async def test_get_unknown_case_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_case(session, "does-not-exist")


async def test_get_case_or_none_returns_none_for_unknown_case(session):
    assert await repository.get_case_or_none(session, "does-not-exist") is None


async def test_list_cases_filters_by_status_and_object_type(session):
    await _make_case(session, case_id="case-1", object_type_id=1)
    open_case_2 = await _make_case(session, case_id="case-2", object_type_id=2)
    closed_case = await _make_case(session, case_id="case-3", object_type_id=1)
    await repository.close_case(session, closed_case, snapshots={})

    all_cases = await repository.list_cases(session)
    assert {c.id for c in all_cases} == {"case-1", "case-2", "case-3"}

    open_cases = await repository.list_cases(session, status="open")
    assert {c.id for c in open_cases} == {"case-1", "case-2"}

    by_type = await repository.list_cases(session, object_type_id=2)
    assert [c.id for c in by_type] == [open_case_2.id]


async def test_add_document_reference(session):
    case = await _make_case(session)
    reference = await repository.add_document_reference(
        session, case.id, document_id="doc-1", added_by="alice"
    )
    assert reference.document_id == "doc-1"
    assert reference.removed_at is None

    references = await repository.list_document_references(session, case.id)
    assert [r.document_id for r in references] == ["doc-1"]


async def test_add_document_reference_to_unknown_case_raises_not_found(session):
    with pytest.raises(repository.NotFoundError):
        await repository.add_document_reference(
            session, "does-not-exist", document_id="doc-1", added_by="alice"
        )


async def test_add_document_reference_to_closed_case_raises_case_closed(session):
    case = await _make_case(session)
    await repository.close_case(session, case, snapshots={})
    with pytest.raises(repository.CaseClosedError):
        await repository.add_document_reference(
            session, case.id, document_id="doc-1", added_by="alice"
        )


async def test_remove_document_reference_soft_deletes(session):
    case = await _make_case(session)
    await repository.add_document_reference(session, case.id, document_id="doc-1", added_by="alice")
    removed = await repository.remove_document_reference(
        session, case.id, "doc-1", removed_by="bob"
    )
    assert removed.removed_by == "bob"
    assert removed.removed_at is not None

    active = await repository.get_active_references(session, case.id)
    assert active == []
    all_references = await repository.list_document_references(session, case.id)
    assert len(all_references) == 1  # weiter sichtbar, nur weich entfernt


async def test_remove_unknown_reference_raises_not_found(session):
    case = await _make_case(session)
    with pytest.raises(repository.NotFoundError):
        await repository.remove_document_reference(
            session, case.id, "does-not-exist", removed_by="bob"
        )


async def test_remove_document_reference_on_closed_case_raises_case_closed(session):
    case = await _make_case(session)
    await repository.add_document_reference(session, case.id, document_id="doc-1", added_by="alice")
    await repository.close_case(session, case, snapshots={})
    with pytest.raises(repository.CaseClosedError):
        await repository.remove_document_reference(session, case.id, "doc-1", removed_by="bob")


async def test_close_case_fixes_snapshot_version_for_active_references_only(session):
    case = await _make_case(session)
    await repository.add_document_reference(session, case.id, document_id="doc-1", added_by="alice")
    await repository.add_document_reference(session, case.id, document_id="doc-2", added_by="alice")
    await repository.remove_document_reference(session, case.id, "doc-2", removed_by="alice")

    closed = await repository.close_case(session, case, snapshots={"doc-1": 3, "doc-2": 9})

    assert closed.status == "closed"
    assert closed.closed_at is not None
    references = {
        r.document_id: r for r in await repository.list_document_references(session, case.id)
    }
    assert references["doc-1"].snapshot_version_number == 3
    # bereits entfernte Referenz bleibt ohne Snapshot, obwohl im Dict enthalten -
    # close_case fixiert bewusst nur noch aktive Referenzen.
    assert references["doc-2"].snapshot_version_number is None


async def test_close_case_leaves_snapshot_none_for_documents_missing_from_snapshots(session):
    case = await _make_case(session)
    await repository.add_document_reference(session, case.id, document_id="doc-1", added_by="alice")
    closed = await repository.close_case(session, case, snapshots={})
    references = await repository.list_document_references(session, closed.id)
    assert references[0].snapshot_version_number is None
