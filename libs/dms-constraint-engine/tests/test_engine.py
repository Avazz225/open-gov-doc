from dms_constraint_engine import ROOT_PARENT_TYPE, validate

RECHNUNG_SCHEMA = {
    "objectType": "Rechnung",
    "appliesTo": "document",
    "attributes": [
        {"name": "Rechnungsnummer", "type": "string", "required": True, "pattern": r"RE-\d{6}"},
        {"name": "Betrag", "type": "decimal", "required": True, "min": 0},
        {"name": "Kostenstelle", "type": "string", "required": False},
    ],
    "namingConstraints": {
        "mustContain": ["Rechnungsnummer"],
        "pattern": "{Rechnungsnummer}_{Datum}",
    },
    "conditions": [{"if": "Betrag > 10000", "then": "require:Kostenstelle"}],
}


def test_valid_document_has_no_errors():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": 500, "Datum": "2026-01-01"},
    )
    assert errors == []


def test_missing_required_attribute_is_reported():
    errors = validate(RECHNUNG_SCHEMA, name="irrelevant.pdf", attributes={})
    assert any("Rechnungsnummer" in e for e in errors)
    assert any("Betrag" in e for e in errors)


def test_pattern_mismatch_is_reported():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="x_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "not-a-match", "Betrag": 1, "Datum": "2026-01-01"},
    )
    assert any("Muster" in e for e in errors)


def test_negative_amount_violates_min():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": -5, "Datum": "2026-01-01"},
    )
    assert any("Mindestwert" in e for e in errors)


def test_non_numeric_amount_is_reported():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": "viel-geld", "Datum": "2026-01-01"},
    )
    assert any("keine gültige Zahl" in e for e in errors)


def test_conditional_requirement_triggers_when_condition_true():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": 20000, "Datum": "2026-01-01"},
    )
    assert any("Kostenstelle" in e for e in errors)


def test_conditional_requirement_not_triggered_when_condition_false():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": 100, "Datum": "2026-01-01"},
    )
    assert errors == []


def test_conditional_requirement_satisfied_passes():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="RE-123456_2026-01-01.pdf",
        attributes={
            "Rechnungsnummer": "RE-123456",
            "Betrag": 20000,
            "Kostenstelle": "K-1",
            "Datum": "2026-01-01",
        },
    )
    assert errors == []


def test_naming_pattern_mismatch_is_reported():
    errors = validate(
        RECHNUNG_SCHEMA,
        name="voelliganders.pdf",
        attributes={"Rechnungsnummer": "RE-123456", "Betrag": 100, "Datum": "2026-01-01"},
    )
    assert any("Namensmuster" in e for e in errors)


def test_naming_must_contain_missing_attribute_is_reported():
    schema = {
        "attributes": [],
        "namingConstraints": {"mustContain": ["Projektnummer"]},
    }
    errors = validate(schema, name="beliebig.pdf", attributes={})
    assert any("Projektnummer" in e for e in errors)


def test_boolean_type_rejects_non_bool():
    schema = {"attributes": [{"name": "Aktiv", "type": "boolean"}]}
    errors = validate(schema, name="x", attributes={"Aktiv": "ja"})
    assert any("Boolean" in e for e in errors)


def test_date_type_rejects_invalid_date():
    schema = {"attributes": [{"name": "Faelligkeit", "type": "date"}]}
    errors = validate(schema, name="x", attributes={"Faelligkeit": "not-a-date"})
    assert any("Datum" in e for e in errors)


def test_reference_type_rejects_empty_value():
    schema = {"attributes": [{"name": "Vorgang", "type": "reference"}]}
    errors = validate(schema, name="x", attributes={"Vorgang": "  "})
    assert any("Referenz-ID" in e for e in errors)


def test_empty_schema_always_valid():
    assert validate({}, name="x", attributes={"anything": "goes"}) == []


def test_no_allowed_parent_types_means_unrestricted_placement():
    schema = {"attributes": []}
    assert validate(schema, name="x", attributes={}, parent_type_name=None) == []
    assert validate(schema, name="x", attributes={}, parent_type_name="Irgendwas") == []
    assert validate(schema, name="x", attributes={}, parent_type_name=ROOT_PARENT_TYPE) == []


def test_allowed_parent_types_accepts_matching_parent():
    schema = {"attributes": [], "allowedParentTypes": ["Projektordner"]}
    errors = validate(schema, name="x", attributes={}, parent_type_name="Projektordner")
    assert errors == []


def test_allowed_parent_types_rejects_non_matching_parent():
    schema = {"attributes": [], "allowedParentTypes": ["Projektordner"]}
    errors = validate(schema, name="x", attributes={}, parent_type_name="Kundenordner")
    assert any("Elternordner" in e for e in errors)


def test_allowed_parent_types_root_sentinel_accepts_root_placement():
    schema = {"attributes": [], "allowedParentTypes": [ROOT_PARENT_TYPE]}
    errors = validate(schema, name="x", attributes={}, parent_type_name=ROOT_PARENT_TYPE)
    assert errors == []


def test_allowed_parent_types_root_sentinel_rejects_untyped_parent():
    # Ein Elternordner ohne eigenen Objekttyp ist NICHT dasselbe wie die Wurzel
    # (2.2a) - nur der explizite ROOT_PARENT_TYPE-Sentinel zählt als Wurzel.
    schema = {"attributes": [], "allowedParentTypes": [ROOT_PARENT_TYPE]}
    errors = validate(schema, name="x", attributes={}, parent_type_name=None)
    assert any("ohne Objekttyp" in e for e in errors)


def test_allowed_parent_types_rejects_untyped_parent_when_specific_type_required():
    schema = {"attributes": [], "allowedParentTypes": ["Projektordner"]}
    errors = validate(schema, name="x", attributes={}, parent_type_name=None)
    assert any("ohne Objekttyp" in e for e in errors)


def test_allowed_parent_types_supports_multiple_alternatives():
    schema = {"attributes": [], "allowedParentTypes": ["Projektordner", ROOT_PARENT_TYPE]}
    assert validate(schema, name="x", attributes={}, parent_type_name="Projektordner") == []
    assert validate(schema, name="x", attributes={}, parent_type_name=ROOT_PARENT_TYPE) == []
    assert validate(schema, name="x", attributes={}, parent_type_name="Anderer") != []
