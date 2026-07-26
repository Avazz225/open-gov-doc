import pytest
from object_type_service import repository
from object_type_service.schemas import ObjectTypeCreate, ObjectTypeUpdate

RECHNUNG = ObjectTypeCreate(
    name="Rechnung",
    applies_to="document",
    attributes=[
        {"name": "Rechnungsnummer", "type": "string", "required": True, "pattern": r"RE-\d{6}"},
        {"name": "Betrag", "type": "decimal", "required": True},
    ],
    conditions=[{"if": "Betrag > 10000", "then": "require:Kostenstelle"}],
)


async def test_create_and_get(session):
    created = await repository.create_object_type(session, RECHNUNG)
    fetched = await repository.get_object_type(session, created.id)
    assert fetched.name == "Rechnung"
    assert fetched.applies_to == "document"
    assert len(fetched.attributes) == 2


async def test_duplicate_name_raises(session):
    await repository.create_object_type(session, RECHNUNG)
    with pytest.raises(repository.DuplicateNameError):
        await repository.create_object_type(session, RECHNUNG)


async def test_get_unknown_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.get_object_type(session, 999999)


async def test_list_filters_by_applies_to(session):
    await repository.create_object_type(session, RECHNUNG)
    await repository.create_object_type(
        session, ObjectTypeCreate(name="Projektordner", applies_to="folder")
    )

    documents = await repository.list_object_types(session, applies_to="document")
    folders = await repository.list_object_types(session, applies_to="folder")

    assert {o.name for o in documents} == {"Rechnung"}
    assert {o.name for o in folders} == {"Projektordner"}


async def test_update_replaces_definition(session):
    created = await repository.create_object_type(session, RECHNUNG)
    updated = await repository.update_object_type(
        session,
        created.id,
        ObjectTypeUpdate(attributes=[{"name": "Neu", "type": "string"}]),
    )
    assert len(updated.attributes) == 1
    assert updated.attributes[0]["name"] == "Neu"


async def test_delete_removes_entry(session):
    created = await repository.create_object_type(session, RECHNUNG)
    await repository.delete_object_type(session, created.id)
    with pytest.raises(repository.NotFoundError):
        await repository.get_object_type(session, created.id)
