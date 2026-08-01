import asyncio
from datetime import UTC, datetime

import pytest
from dms_db_base import make_session_factory
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


async def test_create_with_kennzeichen_format_on_document_type_succeeds(session):
    created = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="RechnungMitKennzeichen",
            applies_to="document",
            kennzeichen_format="{YYYY}-{Laufende_Nummer}",
        ),
    )
    assert created.kennzeichen_format == "{YYYY}-{Laufende_Nummer}"


async def test_create_with_kennzeichen_format_on_folder_type_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="OrdnerMitKennzeichen",
                applies_to="folder",
                kennzeichen_format="{Laufende_Nummer}",
            ),
        )


async def test_create_with_kennzeichen_format_unknown_placeholder_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="MeinDocUnbekannterPlatzhalter",
                applies_to="document",
                kennzeichen_format="{Foo}-{Laufende_Nummer}",
            ),
        )


async def test_create_with_kennzeichen_format_missing_laufende_nummer_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="MeinDocOhneNummer", applies_to="document", kennzeichen_format="{YYYY}"
            ),
        )


async def test_create_with_kennzeichen_display_override_on_folder_type_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="OrdnerMitOverride", applies_to="folder", kennzeichen_display_override=True
            ),
        )


async def test_create_with_required_signature_level_on_folder_type_raises(session):
    with pytest.raises(repository.InvalidFieldError):
        await repository.create_object_type(
            session,
            ObjectTypeCreate(
                name="OrdnerMitSignaturniveau", applies_to="folder", required_signature_level="aes"
            ),
        )


async def test_create_with_required_signature_level_on_document_type_succeeds(session):
    created = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="VertragMitSignaturpflicht", applies_to="document", required_signature_level="aes"
        ),
    )
    assert created.required_signature_level == "aes"


async def test_generate_next_kennzeichen_increments_per_object_type(session):
    created = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="RechnungLaufend",
            applies_to="document",
            kennzeichen_format="{YYYY}-{Laufende_Nummer}",
        ),
    )
    first = await repository.generate_next_kennzeichen(session, created.id)
    second = await repository.generate_next_kennzeichen(session, created.id)
    year = datetime.now(UTC).year
    assert first == f"{year}-001"
    assert second == f"{year}-002"


async def test_generate_next_kennzeichen_is_independent_per_object_type(session):
    first_type = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="TypA", applies_to="document", kennzeichen_format="{Laufende_Nummer}"
        ),
    )
    second_type = await repository.create_object_type(
        session,
        ObjectTypeCreate(
            name="TypB", applies_to="document", kennzeichen_format="{Laufende_Nummer}"
        ),
    )
    await repository.generate_next_kennzeichen(session, first_type.id)
    await repository.generate_next_kennzeichen(session, first_type.id)
    first_third = await repository.generate_next_kennzeichen(session, first_type.id)
    second_first = await repository.generate_next_kennzeichen(session, second_type.id)
    assert first_third == "003"
    assert second_first == "001"


async def test_generate_next_kennzeichen_without_format_raises(session):
    created = await repository.create_object_type(session, RECHNUNG)
    with pytest.raises(repository.NoKennzeichenFormatError):
        await repository.generate_next_kennzeichen(session, created.id)


async def test_generate_next_kennzeichen_unknown_object_type_raises(session):
    with pytest.raises(repository.NotFoundError):
        await repository.generate_next_kennzeichen(session, 999999)


async def test_generate_next_kennzeichen_concurrent_calls_are_serialized(engine):
    factory = make_session_factory(engine)
    async with factory() as setup_session:
        created = await repository.create_object_type(
            setup_session,
            ObjectTypeCreate(
                name="KonkurrenzTyp", applies_to="document", kennzeichen_format="{Laufende_Nummer}"
            ),
        )
        await setup_session.commit()
        object_type_id = created.id

    async def _generate_one():
        async with factory() as task_session:
            result = await repository.generate_next_kennzeichen(task_session, object_type_id)
            await task_session.commit()
            return result

    results = await asyncio.gather(*[_generate_one() for _ in range(5)])
    assert sorted(results) == [f"{n:03d}" for n in range(1, 6)]


async def test_get_kennzeichen_config_creates_default_on_first_read(session):
    config = await repository.get_kennzeichen_config(session)
    assert config.show_before_filename is True


async def test_update_kennzeichen_config_persists(session):
    await repository.update_kennzeichen_config(session, show_before_filename=False)
    config = await repository.get_kennzeichen_config(session)
    assert config.show_before_filename is False
