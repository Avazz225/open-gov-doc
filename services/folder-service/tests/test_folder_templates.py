from datetime import UTC, datetime

import pytest
from folder_service import repository
from folder_service.settings import ROOT_FOLDER_ID


async def _make_subtree(session):
    """Aktenplan-artiger Teilbaum: Projekte -> {Vertraege (Objekttyp 7),
    Korrespondenz -> Eingang} - zwei Ebenen, ein Blattknoten mit Objekttyp."""
    projekte = await repository.create_folder(
        session,
        name="Projekte",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await repository.create_folder(
        session,
        name="Vertraege",
        parent_id=projekte.id,
        object_type_id=7,
        attributes={},
        created_by="alice",
    )
    korrespondenz = await repository.create_folder(
        session,
        name="Korrespondenz",
        parent_id=projekte.id,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    await repository.create_folder(
        session,
        name="Eingang",
        parent_id=korrespondenz.id,
        object_type_id=None,
        attributes={},
        created_by="alice",
    )
    return projekte


async def test_build_template_structure_captures_names_and_object_types(session):
    projekte = await _make_subtree(session)

    structure = await repository.build_template_structure(session, projekte.id)

    assert structure["name"] == "Projekte"
    assert structure["object_type_id"] is None
    child_names = {c["name"] for c in structure["children"]}
    assert child_names == {"Vertraege", "Korrespondenz"}
    vertraege = next(c for c in structure["children"] if c["name"] == "Vertraege")
    assert vertraege["object_type_id"] == 7
    assert vertraege["children"] == []
    korrespondenz = next(c for c in structure["children"] if c["name"] == "Korrespondenz")
    assert [c["name"] for c in korrespondenz["children"]] == ["Eingang"]


async def test_build_template_structure_excludes_soft_deleted_children(session):
    projekte = await _make_subtree(session)
    children = await repository.list_children(session, projekte.id)
    vertraege = next(c for c in children if c.name == "Vertraege")
    vertraege.deleted_at = datetime.now(UTC)
    await session.flush()

    structure = await repository.build_template_structure(session, projekte.id)

    assert [c["name"] for c in structure["children"]] == ["Korrespondenz"]


async def test_build_template_structure_unknown_folder_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.build_template_structure(session, "does-not-exist")


async def test_create_list_get_delete_template_roundtrip(session):
    projekte = await _make_subtree(session)
    structure = await repository.build_template_structure(session, projekte.id)

    template = await repository.create_template(
        session,
        name="Aktenplan-Rohbau",
        description="Standard-Struktur für Bauprojekte",
        structure=structure,
        created_by="alice",
    )

    listed = await repository.list_templates(session)
    assert [t.id for t in listed] == [template.id]

    fetched = await repository.get_template(session, template.id)
    assert fetched.structure["name"] == "Projekte"

    await repository.delete_template(session, template.id)
    assert await repository.list_templates(session) == []


async def test_get_template_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_template(session, "does-not-exist")


async def test_apply_template_recreates_full_structure_under_target(session):
    projekte = await _make_subtree(session)
    structure = await repository.build_template_structure(session, projekte.id)
    template = await repository.create_template(
        session, name="Vorlage", description=None, structure=structure, created_by="alice"
    )
    target = await repository.create_folder(
        session,
        name="Neues Projekt",
        parent_id=ROOT_FOLDER_ID,
        object_type_id=None,
        attributes={},
        created_by="bob",
    )

    created = await repository.apply_template(
        session, template, target_parent_id=target.id, created_by="bob"
    )

    assert len(created) == 4
    root_of_tree = created[0]
    assert root_of_tree.name == "Projekte"
    assert root_of_tree.parent_id == target.id
    assert root_of_tree.attributes == {}

    children = await repository.list_children(session, root_of_tree.id)
    names = {c.name for c in children}
    assert names == {"Vertraege", "Korrespondenz"}
    vertraege = next(c for c in children if c.name == "Vertraege")
    assert vertraege.object_type_id == 7
    assert vertraege.attributes == {}

    # Ursprünglicher Quellbaum bleibt unangetastet - Anwendung ist eine reine
    # Kopie, keine Verschiebung.
    original_children = await repository.list_children(session, projekte.id)
    assert {c.name for c in original_children} == {"Vertraege", "Korrespondenz"}


async def test_apply_template_unknown_target_raises(session):
    projekte = await _make_subtree(session)
    structure = await repository.build_template_structure(session, projekte.id)
    template = await repository.create_template(
        session, name="Vorlage", description=None, structure=structure, created_by="alice"
    )

    with pytest.raises(repository.NotFoundError):
        await repository.apply_template(
            session, template, target_parent_id="does-not-exist", created_by="bob"
        )
