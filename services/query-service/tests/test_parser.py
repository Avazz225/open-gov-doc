import pytest
from query_service.parser import ParsedQuery, ParserPluginError, load_parser_plugin


def test_load_parser_plugin_returns_none_without_module():
    assert load_parser_plugin(None) is None
    assert load_parser_plugin("") is None


def test_load_parser_plugin_raises_for_unknown_module():
    with pytest.raises(ImportError):
        load_parser_plugin("this_module_does_not_exist_anywhere")


def test_fake_plugin_parses_minimal_select():
    plugin = load_parser_plugin("fake_parser_plugin")
    assert plugin is not None
    parsed = plugin.parse("SELECT * FROM events WHERE actor = 'alice' LIMIT 5")
    assert parsed == ParsedQuery(table="events", filters={"actor": "alice"}, limit=5)


def test_fake_plugin_parses_multiple_conditions_without_limit():
    plugin = load_parser_plugin("fake_parser_plugin")
    parsed = plugin.parse("SELECT * FROM events WHERE actor = 'alice' AND subject = 'doc-1'")
    assert parsed.table == "events"
    assert parsed.filters == {"actor": "alice", "subject": "doc-1"}
    assert parsed.limit is None


def test_fake_plugin_raises_parser_plugin_error_on_garbage_input():
    plugin = load_parser_plugin("fake_parser_plugin")
    with pytest.raises(ParserPluginError):
        plugin.parse("not a query at all")
