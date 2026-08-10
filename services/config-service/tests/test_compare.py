"""Reine Unit-Tests für die Vergleichslogik (7.5, P14-S1) - anders als
`test_api.py` ohne laufenden Container: `compare.py` hat keine eigene
Abhängigkeit zu einem Owner-Service, operiert nur auf bereits vorliegenden
`ConfigDocument`-Objekten."""

from datetime import UTC, datetime

from config_service import compare
from config_service.schemas import (
    ApprovalConfigExport,
    ConfigDocument,
    FederationConfigExport,
    ObjectTypeExport,
    RoleExport,
    SensorConfigExport,
)


def _doc(**kwargs) -> ConfigDocument:
    return ConfigDocument(exported_at=datetime.now(UTC), **kwargs)


def test_normalize_without_pattern_returns_value_unchanged():
    assert compare.normalize("100_testobjekt_typ_alpha", None) == "100_testobjekt_typ_alpha"


def test_normalize_strips_numeric_prefix_example_from_konzept_7_5():
    pattern = r"^\d+_+"
    assert compare.normalize("100_testobjekt_typ_alpha", pattern) == "testobjekt_typ_alpha"
    assert compare.normalize("101__testobjekt_typ_alpha", pattern) == "testobjekt_typ_alpha"


def test_resolve_pattern_prefers_category_specific_over_global():
    ignore_regex = {"*": r"^GLOBAL_", "object_types": r"^SPECIFIC_"}
    assert compare.resolve_pattern("object_types", ignore_regex) == r"^SPECIFIC_"
    assert compare.resolve_pattern("roles", ignore_regex) == r"^GLOBAL_"
    assert compare.resolve_pattern("roles", None) is None


def test_diff_list_category_only_in_base():
    base = [{"name": "a", "applies_to": "document"}]
    delta = compare.diff_list_category("object_types", base, None, ignore_regex=None)
    assert delta.only_in_base == ["a"]
    assert delta.only_in_compare == []
    assert delta.differing == {}
    assert delta.identical == []


def test_diff_list_category_only_in_compare():
    compare_items = [{"name": "a", "applies_to": "document"}]
    delta = compare.diff_list_category("object_types", None, compare_items, ignore_regex=None)
    assert delta.only_in_compare == ["a"]
    assert delta.only_in_base == []


def test_diff_list_category_identical():
    item = {"name": "a", "applies_to": "document"}
    delta = compare.diff_list_category("object_types", [item], [dict(item)], ignore_regex=None)
    assert delta.identical == ["a"]
    assert delta.differing == {}


def test_diff_list_category_differing_reports_only_changed_fields():
    base = [{"name": "a", "applies_to": "document", "icon": "old-icon"}]
    compare_items = [{"name": "a", "applies_to": "document", "icon": "new-icon"}]
    delta = compare.diff_list_category("object_types", base, compare_items, ignore_regex=None)
    assert delta.identical == []
    assert delta.differing == {"a": {"icon": {"base": "old-icon", "compare": "new-icon"}}}


def test_diff_list_category_matches_via_ignore_regex_and_still_diffs_remaining_fields():
    """Das Konzept-7.5-Beispiel selbst: unterschiedliche numerische Präfixe
    gelten dank Ignore-Regex als dasselbe Objekt, inhaltliche Abweichungen an
    den übrigen Attributen werden trotzdem vollständig erkannt (7.5: "der
    eigentliche inhaltliche Vergleich ... erfolgt davon unabhängig weiterhin
    vollständig")."""
    base = [{"name": "100_testobjekt_typ_alpha", "applies_to": "document", "icon": "a"}]
    compare_items = [{"name": "101__testobjekt_typ_alpha", "applies_to": "document", "icon": "b"}]
    delta = compare.diff_list_category(
        "object_types", base, compare_items, ignore_regex={"*": r"^\d+_+"}
    )
    assert delta.only_in_base == []
    assert delta.only_in_compare == []
    # Anzeigename bleibt der rohe Basisinstanz-Name, nicht der normalisierte.
    assert delta.differing == {"100_testobjekt_typ_alpha": {"icon": {"base": "a", "compare": "b"}}}


def test_diff_list_category_without_matching_regex_treats_prefixed_names_as_distinct():
    base = [{"name": "100_testobjekt_typ_alpha", "applies_to": "document"}]
    compare_items = [{"name": "101__testobjekt_typ_alpha", "applies_to": "document"}]
    delta = compare.diff_list_category("object_types", base, compare_items, ignore_regex=None)
    assert delta.only_in_base == ["100_testobjekt_typ_alpha"]
    assert delta.only_in_compare == ["101__testobjekt_typ_alpha"]


def test_diff_singleton_category_identical():
    delta = compare.diff_singleton_category(
        "federation_config", {"version": "1.0"}, {"version": "1.0"}
    )
    assert delta.identical == ["federation_config"]


def test_diff_singleton_category_differing():
    delta = compare.diff_singleton_category(
        "federation_config", {"version": "1.0"}, {"version": "2.0"}
    )
    assert delta.differing == {"federation_config": {"version": {"base": "1.0", "compare": "2.0"}}}


def test_diff_singleton_category_only_in_one_side():
    assert compare.diff_singleton_category("sensor_config", None, {"x": 1}).only_in_compare == [
        "sensor_config"
    ]
    assert compare.diff_singleton_category("sensor_config", {"x": 1}, None).only_in_base == [
        "sensor_config"
    ]


def test_diff_singleton_category_both_absent_is_fully_empty():
    delta = compare.diff_singleton_category("sensor_config", None, None)
    assert delta.only_in_base == []
    assert delta.only_in_compare == []
    assert delta.differing == {}
    assert delta.identical == []


def test_compare_documents_covers_all_six_categories():
    base = _doc(
        object_types=[ObjectTypeExport(name="a", applies_to="document")],
        roles=[RoleExport(name="r1", description="d", permissions=["read"])],
        approval_config=[ApprovalConfigExport(action_type="x", requires_approval=False)],
        sensor_config=SensorConfigExport(global_default=True, overrides={}),
        federation_config=FederationConfigExport(version="1.0", min_compatible_peer_version="1.0"),
    )
    compare_doc = _doc(
        object_types=[ObjectTypeExport(name="a", applies_to="document")],
        roles=[RoleExport(name="r1", description="d", permissions=["read", "write"])],
        approval_config=[ApprovalConfigExport(action_type="x", requires_approval=True)],
        sensor_config=SensorConfigExport(global_default=False, overrides={}),
        federation_config=FederationConfigExport(version="2.0", min_compatible_peer_version="1.0"),
    )
    result = compare.compare_documents(
        base,
        compare_doc,
        categories={
            "object_types",
            "workflows",
            "roles",
            "approval_config",
            "sensor_config",
            "federation_config",
        },
    )
    # Kategorie wurde angefragt, aber auf keiner Seite exportiert -> Delta
    # bleibt vollständig leer statt den Schlüssel stillschweigend wegzulassen.
    assert result["workflows"] == compare.diff_list_category(
        "workflows", None, None, ignore_regex=None
    )
    assert result["object_types"].identical == ["a"]
    assert "r1" in result["roles"].differing
    assert "x" in result["approval_config"].differing
    assert result["sensor_config"].differing["sensor_config"]["global_default"] == {
        "base": True,
        "compare": False,
    }
    assert result["federation_config"].differing["federation_config"]["version"] == {
        "base": "1.0",
        "compare": "2.0",
    }
