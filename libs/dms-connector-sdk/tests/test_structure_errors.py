"""A refused create or move becomes StructureRuleError (P91-S2a): pure, no I/O."""

import httpx
import pytest
from dms_connector_sdk import StructureRuleError
from dms_connector_sdk.dms_tree_client import _raise_for_structure


def test_422_with_errors_becomes_structure_rule_error():
    response = httpx.Response(
        422, json={"detail": {"errors": ["Unter „Team“ sind nur Ordner dieser Klassen erlaubt"]}}
    )
    with pytest.raises(StructureRuleError) as caught:
        _raise_for_structure(response)
    assert caught.value.errors == ["Unter „Team“ sind nur Ordner dieser Klassen erlaubt"]


def test_422_with_a_plain_detail_still_carries_it():
    with pytest.raises(StructureRuleError, match="kaputt"):
        _raise_for_structure(httpx.Response(422, json={"detail": "kaputt"}))


def test_other_statuses_pass():
    _raise_for_structure(httpx.Response(201, json={}))
    _raise_for_structure(httpx.Response(400, json={"detail": "x"}))


def test_hidden_count_header_is_read_and_missing_means_zero():
    from dms_connector_sdk.dms_tree_client import _hidden_count

    assert _hidden_count(httpx.Response(200, headers={"X-DMS-Hidden-Count": "3"})) == 3
    assert _hidden_count(httpx.Response(200)) == 0
    assert _hidden_count(httpx.Response(200, headers={"X-DMS-Hidden-Count": "x"})) == 0
