from object_type_service.layout import (
    COLUMNS_PER_ROW,
    DEFAULT_RESPONSIVE_BREAKPOINT_PX,
    generate_smart_layout,
)


def test_generate_smart_layout_empty_attributes():
    layout = generate_smart_layout([])
    assert layout == {"rows": [], "responsive_breakpoint_px": DEFAULT_RESPONSIVE_BREAKPOINT_PX}


def test_generate_smart_layout_packs_columns_per_row():
    attributes = [
        {"name": "Rechnungsnummer", "type": "string", "required": True},
        {"name": "Betrag", "type": "decimal", "required": True},
        {"name": "Kostenstelle", "type": "string", "required": False},
    ]
    layout = generate_smart_layout(attributes)
    assert len(layout["rows"]) == 2
    assert len(layout["rows"][0]["columns"]) == COLUMNS_PER_ROW
    assert len(layout["rows"][1]["columns"]) == 1


def test_generate_smart_layout_uses_attribute_name_as_label_and_default():
    attributes = [{"name": "Betrag", "type": "decimal", "required": True}]
    layout = generate_smart_layout(attributes)
    field = layout["rows"][0]["columns"][0]
    assert field == {"attribute": "Betrag", "label": "Betrag", "required": True}


def test_generate_smart_layout_defaults_missing_required_to_false():
    attributes = [{"name": "Kostenstelle", "type": "string"}]
    layout = generate_smart_layout(attributes)
    assert layout["rows"][0]["columns"][0]["required"] is False
