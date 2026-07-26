import re
from datetime import date
from typing import Any

# Erweiterbarkeit (4.5: "sollte erweiterbar sein um eigene Validierungs-Plugins"):
# neue Attributtypen werden hier als weiterer Eintrag ergänzt, nicht über eine
# generische Plugin-Ladeinfrastruktur - für den aktuellen Bedarf ausreichend,
# ein echter Plugin-Lademechanismus wäre verfrühte Komplexität.
_NUMERIC_TYPES = {"decimal", "integer"}

_CONDITION_RE = re.compile(r"^\s*([A-Za-z0-9_äöüÄÖÜß]+)\s*(>=|<=|==|!=|>|<)\s*(.+?)\s*$")


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
        # Bewusste Vereinfachung: nur Formatprüfung (nicht-leerer String), keine
        # Existenzprüfung gegen den referenzierten Service - würde eine generische
        # Auflösung "Referenztyp -> zuständiger Service" voraussetzen, die es
        # noch nicht gibt. Siehe docs/services/object-type-service.md.
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

    # re.escape() escapiert auch die von uns eingefügten Platzhalter-Klammern zu
    # "\{"/"\}" - das Ersetzungsmuster muss diese escapte Form matchen.
    regex_source = re.sub(
        r"\\\{([A-Za-z0-9_äöüÄÖÜß]+)\\\}", _escape_and_substitute, re.escape(pattern)
    )
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if not re.fullmatch(regex_source, stem):
        errors.append(f"Name {name!r} erfüllt nicht das Namensmuster {pattern!r}")


def _evaluate_condition(expression: str, attributes: dict[str, Any]) -> bool:
    match = _CONDITION_RE.match(expression)
    if match is None:
        return False
    attr, op, literal = match.groups()
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
    if action.startswith("require:"):
        required_attr = action.removeprefix("require:").strip()
        if not attributes.get(required_attr):
            errors.append(f"Bedingte Pflichtangabe fehlt: '{required_attr}'")


def validate(schema: dict, *, name: str, attributes: dict[str, Any]) -> list[str]:
    """Validiert ``attributes`` (und ``name``) gegen ein Objekttyp-Schema (2.2).

    Unterstützt Pflichtfelder, bedingte Pflichtfelder, Musterprüfung für Namen
    und Werte, Wertebereiche (min/max) - die in 4.5 als Minimum geforderten
    Regeltypen. Gibt eine Liste menschenlesbarer Fehlermeldungen zurück
    (leer = gültig).
    """
    errors: list[str] = []

    for attr_def in schema.get("attributes", []):
        _validate_attribute(attr_def, attributes, errors)

    _validate_naming(schema, name, attributes, errors)

    for condition in schema.get("conditions", []):
        if _evaluate_condition(condition["if"], attributes):
            _apply_then(condition["then"], attributes, errors)

    return errors
