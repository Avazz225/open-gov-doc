"""Parent-side structure rules (P91-S2a, ADR 0255): pure, no I/O."""

from dms_constraint_engine import ROOT_PARENT_TYPE, allowed_children, validate_child

TEAM = {
    "label": "Team",
    "allowedChildFolderTypes": ["Vorgang"],
    "allowedDocumentTypes": None,
    "childrenFromTemplateOnly": False,
}


def test_untyped_parent_accepts_everything():
    assert validate_child(None, "folder", None) == []
    assert validate_child(None, "document", "Rechnung") == []


def test_open_lists_accept_untyped_children():
    assert validate_child(TEAM, "document", None) == []


def test_list_admits_only_listed_types_and_refuses_untyped():
    assert validate_child(TEAM, "folder", "Vorgang") == []
    assert "nicht „Abteilung“" in validate_child(TEAM, "folder", "Abteilung")[0]
    assert "ohne Objekttyp" in validate_child(TEAM, "folder", None)[0]


def test_empty_list_admits_nothing():
    rules = {**TEAM, "allowedDocumentTypes": []}
    assert validate_child(rules, "document", "Akte") == [
        "Unter „Team“ sind keine Dokumente erlaubt"
    ]


def test_template_only_needs_a_template():
    rules = {**TEAM, "childrenFromTemplateOnly": True}
    assert "Strukturvorlage" in validate_child(rules, "folder", "Vorgang")[0]
    assert validate_child(rules, "folder", "Vorgang", via_template=True) == []
    # Documents are uploaded, not templated: the flag doesn't touch them.
    assert validate_child(rules, "document", None) == []


def test_allowed_children_intersects_both_sides():
    candidates = [
        {"name": "Vorgang", "appliesTo": "folder", "allowedParentTypes": ["Team"]},
        # Listed by the parent, but its own rule excludes Team: unreachable.
        {"name": "Abteilung", "appliesTo": "folder", "allowedParentTypes": [ROOT_PARENT_TYPE]},
        {"name": "Notiz", "appliesTo": "folder", "allowedParentTypes": None},
        {"name": "Akte", "appliesTo": "document", "allowedParentTypes": ["Vorgang"]},
        {"name": "Brief", "appliesTo": "document", "allowedParentTypes": None},
    ]
    rules = {**TEAM, "allowedChildFolderTypes": ["Vorgang", "Abteilung"]}
    result = allowed_children(rules, "Team", candidates)
    assert result["folder_types"] == ["Vorgang"]
    assert result["document_types"] == ["Brief"]
    assert result["untyped_folders"] is False
    assert result["untyped_documents"] is True


def test_allowed_children_under_an_untyped_parent_uses_child_rules_only():
    candidates = [
        {"name": "Vorgang", "appliesTo": "folder", "allowedParentTypes": ["Team"]},
        {"name": "Notiz", "appliesTo": "folder", "allowedParentTypes": None},
    ]
    result = allowed_children(None, None, candidates)
    assert result["folder_types"] == ["Notiz"]
    assert result["untyped_folders"] is True
