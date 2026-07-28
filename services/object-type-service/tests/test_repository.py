import pytest
from object_type_service import repository
from object_type_service.schemas import (
    LayoutField,
    LayoutIn,
    LayoutRow,
    ObjectTypeCreate,
    ObjectTypeUpdate,
)

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


async def test_create_with_root_sentinel_allowed_parent_type_succeeds(session):
    created = await repository.create_object_type(
        session,
        ObjectTypeCreate(name="TopLevelOrd", applies_to="folder", allowed_parent_types=["$ROOT"]),
    )
    assert created.allowed_parent_types == ["$ROOT"]


async def test_create_with_allowed_parent_types_referencing_existing_folder_type_succeeds(session):
    await repository.create_object_type(
        session, ObjectTypeCreate(name="TopLevelOrd", applies_to="folder")
    )
    created = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="SecondLevelOrd", applies_to="folder", allowed_parent_types=["TopLevelOrd"]
        ),
    )
    assert created.allowed_parent_types == ["TopLevelOrd"]


async def test_create_with_allowed_parent_types_referencing_unknown_type_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="MeinDoc", applies_to="document", allowed_parent_types=["Unbekannt"]
            ),
        )


async def test_create_with_allowed_parent_types_referencing_document_type_raises(session):
    await repository.create_object_type(session, RECHNUNG)
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="AndereRechnung", applies_to="document", allowed_parent_types=["Rechnung"]
            ),
        )


async def test_create_icon_on_document_type_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session, ObjectTypeCreate(name="MeinDoc2", applies_to="document", icon="file")
        )


async def test_create_icon_on_folder_type_succeeds(session):
    created = await repository.create_object_type(
        session, ObjectTypeCreate(name="Projektordner2", applies_to="folder", icon="folder-star")
    )
    assert created.icon == "folder-star"


async def test_update_allowed_parent_types_and_icon(session):
    parent = await repository.create_object_type(
        session, ObjectTypeCreate(name="TopLevelOrd2", applies_to="folder")
    )
    created = await repository.create_object_type(
        session, ObjectTypeCreate(name="SecondLevelOrd2", applies_to="folder")
    )
    updated = await repository.update_object_type(
        session,
        created.id,
        ObjectTypeUpdate(allowed_parent_types=[parent.name], icon="folder-plain"),
    )
    assert updated.allowed_parent_types == [parent.name]
    assert updated.icon == "folder-plain"


async def test_get_layout_without_override_returns_none(session):
    created = await repository.create_object_type(session, RECHNUNG)
    assert await repository.get_layout(session, created.id, "display") is None


async def test_upsert_layout_persists_override(session):
    created = await repository.create_object_type(session, RECHNUNG)
    payload = LayoutIn(
        rows=[
            LayoutRow(
                columns=[
                    LayoutField(attribute="Rechnungsnummer", label="Rechnungs-Nr.", required=True)
                ]
            )
        ],
        responsive_breakpoint_px=480,
    )
    saved = await repository.upsert_layout(session, created.id, "upload", payload)
    assert saved.layout["responsive_breakpoint_px"] == 480
    assert saved.layout["rows"][0]["columns"][0]["label"] == "Rechnungs-Nr."

    fetched = await repository.get_layout(session, created.id, "upload")
    assert fetched is not None
    assert fetched.layout["rows"][0]["columns"][0]["attribute"] == "Rechnungsnummer"


async def test_upsert_layout_overwrites_existing(session):
    created = await repository.create_object_type(session, RECHNUNG)
    first = LayoutIn(
        rows=[LayoutRow(columns=[LayoutField(attribute="Betrag", label="Betrag")])],
    )
    await repository.upsert_layout(session, created.id, "display", first)
    second = LayoutIn(
        rows=[LayoutRow(columns=[LayoutField(attribute="Rechnungsnummer", label="Nr.")])],
    )
    updated = await repository.upsert_layout(session, created.id, "display", second)
    assert updated.layout["rows"][0]["columns"][0]["attribute"] == "Rechnungsnummer"


async def test_upsert_layout_with_unknown_attribute_raises(session):
    created = await repository.create_object_type(session, RECHNUNG)
    payload = LayoutIn(
        rows=[LayoutRow(columns=[LayoutField(attribute="Unbekannt", label="Unbekannt")])],
    )
    with pytest.raises(repository.InvalidFieldError):
        await repository.upsert_layout(session, created.id, "search", payload)


async def test_upsert_layout_unknown_object_type_raises(session):
    payload = LayoutIn(rows=[])
    with pytest.raises(repository.NotFoundError):
        await repository.upsert_layout(session, 999999, "display", payload)


async def test_delete_layout_removes_override(session):
    created = await repository.create_object_type(session, RECHNUNG)
    payload = LayoutIn(
        rows=[LayoutRow(columns=[LayoutField(attribute="Betrag", label="Betrag")])],
    )
    await repository.upsert_layout(session, created.id, "display", payload)
    await repository.delete_layout(session, created.id, "display")
    assert await repository.get_layout(session, created.id, "display") is None


async def test_delete_layout_without_override_is_idempotent(session):
    created = await repository.create_object_type(session, RECHNUNG)
    await repository.delete_layout(session, created.id, "display")
    assert await repository.get_layout(session, created.id, "display") is None
