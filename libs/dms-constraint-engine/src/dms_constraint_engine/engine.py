import re
from datetime import date
from typing import Any

# Extensibility (4.5: "should be extensible with custom validation plugins"):
# new attribute types are added here as another entry, not via a generic
# plugin-loading infrastructure - sufficient for current needs; a real
# plugin-loading mechanism would be premature complexity.
_NUMERIC_TYPES = {"decimal", "integer"}

_CONDITION_RE = re.compile(r"^\s*([A-Za-z0-9_äöüÄÖÜß]+)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")

# Sentinel for "placed directly under the root" in ``allowedParentTypes``
# (2.2a) - not a real object type name, hence a constant instead of free text.
ROOT_PARENT_TYPE = "$ROOT"


def _to_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_attribute(attr_def: dict, attributes: dict[str, Any], errors: list[str]) -> None:
    name = attr_def["name"]
    value = attributes.get(name)
    required = attr_def.get("required", False)

    if value is None or value == "":
        if required:
            errors.append(f"Pflichtfeld '{name}' fehlt")
        return

    attr_type = attr_def.get("type", "string")

    if attr_type == "string":
        pattern = attr_def.get("pattern")
        if pattern and not re.fullmatch(pattern, str(value)):
            errors.append(f"Attribut '{name}' erfüllt nicht das Muster {pattern!r}")
    elif attr_type in _NUMERIC_TYPES:
        number = _to_number(value)
        if number is None:
            errors.append(f"Attribut '{name}' ist keine gültige Zahl: {value!r}")
            return
        if attr_type == "integer" and number != int(number):
            errors.append(f"Attribut '{name}' muss eine Ganzzahl sein: {value!r}")
        minimum = attr_def.get("min")
        maximum = attr_def.get("max")
        if minimum is not None and number < minimum:
            errors.append(f"Attribut '{name}' unterschreitet den Mindestwert {minimum}")
        if maximum is not None and number > maximum:
            errors.append(f"Attribut '{name}' überschreitet den Höchstwert {maximum}")
    elif attr_type == "boolean":
        if not isinstance(value, bool):
            errors.append(f"Attribut '{name}' muss ein Boolean sein: {value!r}")
    elif attr_type == "date":
        try:
            date.fromisoformat(str(value))
        except ValueError:
            errors.append(f"Attribut '{name}' ist kein gültiges ISO-Datum: {value!r}")
    elif attr_type == "reference":
        # Deliberate simplification: only format validation (non-empty string),
        # no existence check against the referenced service - would require a
        # generic "reference type -> responsible service" resolution that
        # doesn't exist yet. See docs/services/object-type-service.md.
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Attribut '{name}' muss eine nicht-leere Referenz-ID sein")


def _validate_naming(
    schema: dict, name: str, attributes: dict[str, Any], errors: list[str]
) -> None:
    naming = schema.get("namingConstraints")
    if not naming:
        return

    for required_attr in naming.get("mustContain", []):
        if not attributes.get(required_attr):
            errors.append(
                f"Name erfordert gesetztes Attribut '{required_attr}' (namingConstraints)"
            )

    pattern = naming.get("pattern")
    if not pattern:
        return

    def _escape_and_substitute(match: re.Match) -> str:
        field = match.group(1)
        value = attributes.get(field)
        return re.escape(str(value)) if value is not None else ".+"

    # re.escape() also escapes the placeholder braces we inserted into
    # "\{"/"\}" - the substitution pattern must match this escaped form.
    regex_source = re.sub(
        r"\\\{([A-Za-z0-9_äöüÄÖÜß]+)\\\}", _escape_and_substitute, re.escape(pattern)
    )
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if not re.fullmatch(regex_source, stem):
        errors.append(f"Name {name!r} erfüllt nicht das Namensmuster {pattern!r}")


CONDITION_OPERATORS = (">=", "<=", "==", "!=", ">", "<")
REQUIRE_PREFIX = "require:"


def parse_condition(expression: str) -> tuple[str, str, str] | None:
    """`"<attribute> <op> <literal>"` as `(attribute, op, literal)`, or None
    when the expression can't be evaluated (P88-S3: object-type-service
    rejects such a condition on save instead of it silently never firing;
    admin-ui's guided form builds and reads the same shape)."""
    match = _CONDITION_RE.match(expression)
    return match.groups() if match else None


def _evaluate_condition(expression: str, attributes: dict[str, Any]) -> bool:
    parsed = parse_condition(expression)
    if parsed is None:
        return False
    attr, op, literal = parsed
    value = attributes.get(attr)
    if value is None:
        return False

    left = _to_number(value)
    right = _to_number(literal)
    if left is None or right is None:
        left, right = str(value), literal.strip("\"'")

    if op == ">":
        return left > right
    if op == "<":
        return left < right
    if op == ">=":
        return left >= right
    if op == "<=":
        return left <= right
    if op == "==":
        return left == right
    return left != right  # "!="


def _apply_then(action: str, attributes: dict[str, Any], errors: list[str]) -> None:
    if action.startswith(REQUIRE_PREFIX):
        required_attr = action.removeprefix(REQUIRE_PREFIX).strip()
        if not attributes.get(required_attr):
            errors.append(f"Bedingte Pflichtangabe fehlt: '{required_attr}'")


def _validate_status_transition(
    schema: dict,
    from_status: str | None,
    to_status: str | None,
    attributes: dict[str, Any],
    errors: list[str],
) -> None:
    """Status transitions (4.5: "creation, modification, **and status
    transitions**", 7.1: BPMN steps carry the associated status transitions
    through the same constraint checks - Phase 45 Session 4, ADR 0165's
    successor). ``statusTransitions`` entries are matched by exact
    ``from``/``to`` pair; a transition with no matching entry is
    unconditionally allowed (backward compatibility, same "absence = no
    restriction" default as ``allowedParentTypes``). Only rule type built so
    far: ``requiredAttributes`` - the same "missing/empty" check as a plain
    required attribute (see ``_validate_attribute``), just scoped to this
    specific transition instead of unconditionally."""
    if from_status is None and to_status is None:
        return
    for transition in schema.get("statusTransitions", []):
        if transition.get("from") != from_status or transition.get("to") != to_status:
            continue
        for required_attr in transition.get("requiredAttributes", []):
            value = attributes.get(required_attr)
            if value is None or value == "":
                errors.append(
                    f"Statusübergang {from_status!r} -> {to_status!r} erfordert gesetztes "
                    f"Attribut '{required_attr}'"
                )


def _validate_parent(schema: dict, parent_type_name: str | None, errors: list[str]) -> None:
    """Enforced object hierarchy (2.2a): ``allowedParentTypes`` lists the
    names of permitted parent folder classes, ``ROOT_PARENT_TYPE`` stands for
    placement directly under the root. If ``allowedParentTypes`` is missing
    or the list is empty, the type can be placed anywhere (backward
    compatibility). ``parent_type_name=None`` means "parent folder without
    its own object type" (not an alias for the root - ``ROOT_PARENT_TYPE`` is
    responsible for that)."""
    allowed = schema.get("allowedParentTypes")
    if not allowed:
        return
    if parent_type_name not in allowed:
        label = "ohne Objekttyp" if parent_type_name is None else repr(parent_type_name)
        errors.append(
            f"Objekttyp erlaubt keine Platzierung unter einem Elternordner {label} "
            f"- erlaubte Elternklassen: {allowed}"
        )


def validate_parent(schema: dict, parent_type_name: str | None) -> list[str]:
    """Public, standalone entry point for JUST the placement-hierarchy rule
    (2.2a) - added in Phase 79 Session 1 so `folder-service`'s structure-
    template application can check parent-child nesting without also
    triggering the required-attribute/naming checks `validate()` bundles in
    (a template node's `attributes` are deliberately still empty at apply
    time, see ADR 0056 - a full `validate()` call would spuriously reject
    every typed node with a required field). Single source of truth for
    the nesting rule either way: this and `validate()` both call the same
    private `_validate_parent`."""
    errors: list[str] = []
    _validate_parent(schema, parent_type_name, errors)
    return errors


def validate(
    schema: dict,
    *,
    name: str,
    attributes: dict[str, Any],
    parent_type_name: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
) -> list[str]:
    """Validates ``attributes`` (and ``name``) against an object type schema (2.2).

    Supports required fields, conditionally required fields, pattern
    validation for names and values, value ranges (min/max) - the rule types
    required as a minimum in 4.5 - as well as the enforced placement
    hierarchy from 2.2a (``allowedParentTypes`` in the schema,
    ``parent_type_name`` as the resolved name of the parent class or
    ``ROOT_PARENT_TYPE`` for the root), and, since Phase 45 Session 4,
    status-transition rules (``statusTransitions`` in the schema,
    ``from_status``/``to_status`` as the transition being attempted - both
    ``None`` means "not a status transition", the default, unchanged
    behavior). Returns a list of human-readable error messages (empty =
    valid).
    """
    errors: list[str] = []

    for attr_def in schema.get("attributes", []):
        _validate_attribute(attr_def, attributes, errors)

    _validate_naming(schema, name, attributes, errors)

    for condition in schema.get("conditions", []):
        if _evaluate_condition(condition["if"], attributes):
            _apply_then(condition["then"], attributes, errors)

    _validate_parent(schema, parent_type_name, errors)

    _validate_status_transition(schema, from_status, to_status, attributes, errors)

    return errors


# --- Enforced filing structure, parent side (P91-S2a, ADR 0255) ---
#
# A folder class (or the root, via object-type-service's `structure_config`)
# may list which typed children it accepts:
#   {"label": str,
#    "allowedChildFolderTypes": list[str] | None,
#    "allowedDocumentTypes": list[str] | None,
#    "childrenFromTemplateOnly": bool}
# `None` means open (the behaviour before ADR 0255); a list means only these
# typed children, so an untyped child is refused below it. The parent side
# only ever narrows what the child side (`allowedParentTypes`) allows: a
# placement passes when both agree.


def validate_child(
    parent_rules: dict | None,
    child_kind: str,
    child_type_name: str | None,
    *,
    via_template: bool = False,
) -> list[str]:
    """Parent-side check for one new child. ``parent_rules=None`` (an untyped
    parent, or a type that no longer resolves) accepts everything."""
    if parent_rules is None:
        return []
    label = parent_rules.get("label") or "Elternordner"
    errors: list[str] = []
    if child_kind == "folder":
        if parent_rules.get("childrenFromTemplateOnly") and not via_template:
            errors.append(
                f"Unter „{label}“ können Ordner nur aus einer Strukturvorlage angelegt werden"
            )
        allowed = parent_rules.get("allowedChildFolderTypes")
        noun = "Ordner"
    elif child_kind == "document":
        allowed = parent_rules.get("allowedDocumentTypes")
        noun = "Dokumente"
    else:
        raise ValueError(f"unknown child kind {child_kind!r}")
    if allowed is None:
        return errors
    if not allowed:
        errors.append(f"Unter „{label}“ sind keine {noun} erlaubt")
    elif child_type_name is None:
        errors.append(
            f"Unter „{label}“ sind nur {noun} dieser Klassen erlaubt: {', '.join(allowed)} "
            "(ohne Objekttyp nicht zulässig)"
        )
    elif child_type_name not in allowed:
        errors.append(
            f"Unter „{label}“ sind nur {noun} dieser Klassen erlaubt: {', '.join(allowed)} "
            f"(nicht „{child_type_name}“)"
        )
    return errors


def allowed_children(
    parent_rules: dict | None, parent_type_name: str | None, candidates: list[dict]
) -> dict:
    """What may be created under a parent, for "Neuer Ordner"/upload forms:
    every candidate type (``{"name", "appliesTo", "allowedParentTypes"}``)
    that passes both its own placement rule and the parent's lists, and
    whether untyped folders/documents are still accepted there.
    ``parent_type_name`` is the parent's class name, ``ROOT_PARENT_TYPE`` for
    the root, or ``None`` for an untyped parent."""
    result: dict = {"folder_types": [], "document_types": []}
    for candidate in candidates:
        kind = candidate.get("appliesTo")
        if kind not in ("folder", "document"):
            continue
        if _placement_errors(candidate, parent_type_name):
            continue
        if validate_child(parent_rules, kind, candidate["name"], via_template=True):
            continue
        result["folder_types" if kind == "folder" else "document_types"].append(candidate["name"])
    rules = parent_rules or {}
    result["untyped_folders"] = rules.get("allowedChildFolderTypes") is None
    result["untyped_documents"] = rules.get("allowedDocumentTypes") is None
    result["children_from_template_only"] = bool(rules.get("childrenFromTemplateOnly"))
    return result


def _placement_errors(schema: dict, parent_type_name: str | None) -> list[str]:
    errors: list[str] = []
    _validate_parent(schema, parent_type_name, errors)
    return errors


# --- Evaluating existing data against the structure (P91-S2b, ADR 0255) ---
#
# The conformity report and bulk retyping evaluate many objects at once, so
# they fetch all rules once (object-type-service's object types and
# `structure_config`, as returned by its API) and use these pure helpers
# instead of one `/structure/check` call per object.

ROOT_LABEL = "Ablage (oberste Ebene)"


def parent_rules_from_type(object_type: dict | None) -> dict | None:
    """Parent-side rules of a folder class, from object-type-service's API
    shape. ``None`` (open) for documents, missing types and untyped parents."""
    if not object_type or object_type.get("applies_to") != "folder":
        return None
    return {
        "label": object_type["name"],
        "allowedChildFolderTypes": object_type.get("allowed_child_folder_types"),
        "allowedDocumentTypes": object_type.get("allowed_document_types"),
        "childrenFromTemplateOnly": bool(object_type.get("children_from_template_only")),
    }


def root_rules_from_config(structure_config: dict) -> dict:
    return {
        "label": ROOT_LABEL,
        "allowedChildFolderTypes": structure_config.get("root_allowed_folder_types"),
        "allowedDocumentTypes": structure_config.get("root_allowed_document_types"),
        "childrenFromTemplateOnly": False,
    }


def placement_errors(
    child_kind: str,
    child_type: dict | None,
    parent_rules: dict | None,
    parent_type_name: str | None,
) -> list[str]:
    """Both sides for an object that already exists (or is being retyped):
    the parent's lists and the child's own ``allowed_parent_types``. The
    template-only flag concerns creating, not existing, so it is ignored."""
    errors = validate_child(
        parent_rules,
        child_kind,
        child_type["name"] if child_type else None,
        via_template=True,
    )
    if child_type is not None:
        _validate_parent(
            {"allowedParentTypes": child_type.get("allowed_parent_types")},
            parent_type_name,
            errors,
        )
    return errors


def missing_required_attributes(attribute_defs: list[dict], values: dict) -> list[str]:
    """Names of required attributes without a value - what a defaulted or
    template-created object still owes (ADR 0255, Decision 3)."""
    return [
        definition["name"]
        for definition in attribute_defs or []
        if definition.get("required") and values.get(definition["name"]) in (None, "")
    ]
