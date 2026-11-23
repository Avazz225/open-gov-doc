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
