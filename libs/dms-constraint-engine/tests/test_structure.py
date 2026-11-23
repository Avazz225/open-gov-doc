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


def test_placement_errors_checks_both_sides_and_ignores_template_only():
    from dms_constraint_engine import parent_rules_from_type, placement_errors

    team = {
        "name": "Team",
        "applies_to": "folder",
        "allowed_child_folder_types": ["Vorgang"],
        "allowed_document_types": None,
        "children_from_template_only": True,
    }
    vorgang = {"name": "Vorgang", "applies_to": "folder", "allowed_parent_types": ["Team"]}
    abteilung = {"name": "Abteilung", "applies_to": "folder", "allowed_parent_types": ["$ROOT"]}
    rules = parent_rules_from_type(team)
    assert placement_errors("folder", vorgang, rules, "Team") == []
    errors = placement_errors("folder", abteilung, rules, "Team")
    assert len(errors) == 2  # not listed, and its own rule wants the root
    assert placement_errors("folder", None, rules, "Team")  # untyped below a list


def test_root_rules_and_missing_required():
    from dms_constraint_engine import (
        missing_required_attributes,
        placement_errors,
        root_rules_from_config,
    )

    rules = root_rules_from_config(
        {"root_allowed_folder_types": [], "root_allowed_document_types": None}
    )
    assert placement_errors("folder", None, rules, "$ROOT") == [
        "Unter „Ablage (oberste Ebene)“ sind keine Ordner erlaubt"
    ]
    assert placement_errors("document", None, rules, "$ROOT") == []
    defs = [{"name": "Aktenzeichen", "required": True}, {"name": "Notiz"}]
    assert missing_required_attributes(defs, {"Aktenzeichen": ""}) == ["Aktenzeichen"]
    assert missing_required_attributes(defs, {"Aktenzeichen": "AZ-1"}) == []
