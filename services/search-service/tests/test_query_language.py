import pytest
from search_service.query_language import (
    And,
    Fuzzy,
    Not,
    Or,
    Phrase,
    Prefix,
    Proximity,
    QuerySyntaxError,
    Term,
    contains_fuzzy,
    parse_query,
)


def test_none_and_blank_query_parse_to_none():
    assert parse_query(None) is None
    assert parse_query("") is None
    assert parse_query("   ") is None


def test_single_word_is_a_term():
    assert parse_query("Rechnung") == Term("Rechnung")


def test_two_words_are_implicit_and():
    assert parse_query("Rechnung Vertrag") == And(Term("Rechnung"), Term("Vertrag"))


def test_or_keyword_is_case_insensitive():
    assert parse_query("Rechnung or Vertrag") == Or(Term("Rechnung"), Term("Vertrag"))
    assert parse_query("Rechnung OR Vertrag") == Or(Term("Rechnung"), Term("Vertrag"))


def test_and_binds_tighter_than_or():
    # a b or c  =>  (a AND b) OR c
    assert parse_query("a b or c") == Or(And(Term("a"), Term("b")), Term("c"))


def test_minus_prefix_negates_a_single_word():
    assert parse_query("Rechnung -storniert") == And(Term("Rechnung"), Not(Term("storniert")))


def test_not_keyword_is_equivalent_to_minus():
    assert parse_query("Rechnung NOT storniert") == And(Term("Rechnung"), Not(Term("storniert")))


def test_hyphenated_word_is_not_split_into_minus():
    assert parse_query("E-Mail") == Term("E-Mail")


def test_parentheses_override_precedence():
    # a and (b or c)
    assert parse_query("a (b or c)") == And(Term("a"), Or(Term("b"), Term("c")))


def test_unclosed_parenthesis_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("a (b or c")


def test_unclosed_quote_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query('"a b')


def test_phrase_without_tilde():
    assert parse_query('"Rechnung Nr 4711"') == Phrase(("Rechnung", "Nr", "4711"))


def test_prefix_wildcard():
    assert parse_query("Rech*") == Prefix("Rech")


def test_fuzzy_default_level():
    assert parse_query("Rechnnug~") == Fuzzy("Rechnnug", 2)


def test_fuzzy_explicit_level():
    assert parse_query("Rechnnug~1") == Fuzzy("Rechnnug", 1)
    assert parse_query("Rechnnug~3") == Fuzzy("Rechnnug", 3)


def test_fuzzy_invalid_level_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("Rechnnug~4")


def test_proximity_two_words():
    assert parse_query('"Vertrag Kunde"~5') == Proximity("Vertrag", "Kunde", 5)


def test_proximity_requires_exactly_two_words():
    with pytest.raises(QuerySyntaxError):
        parse_query('"a b c"~5')


def test_proximity_distance_is_clamped_to_max():
    node = parse_query('"a b"~9999')
    assert isinstance(node, Proximity)
    assert node.distance == 20


def test_proximity_distance_at_least_one():
    node = parse_query('"a b"~0')
    assert node.distance == 1


def test_trailing_garbage_after_valid_query_raises():
    with pytest.raises(QuerySyntaxError):
        parse_query("a )")


def test_contains_fuzzy_detects_nested_fuzzy():
    node = parse_query("a (b~ or c)")
    assert contains_fuzzy(node) is True


def test_contains_fuzzy_false_without_fuzzy():
    node = parse_query('a (b or "c d") -e f*')
    assert contains_fuzzy(node) is False
