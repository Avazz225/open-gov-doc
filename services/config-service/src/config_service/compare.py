"""Delta-/Vergleichsfunktion zwischen zwei Konfigurationsexporten (7.5,
P14-S1) - rein lesend/diagnostisch, verändert nichts an einer der beiden
Seiten (siehe `docs/adr/0040-config-compare-field-level-diff-no-cross-
installation-fetch.md` für die Architekturentscheidungen: Feld-Ebene statt
Tiefendiff je verschachtelter Liste, kein automatischer Cross-Installation-
Abruf in dieser Session)."""

import re
from typing import Any

from config_service.schemas import CategoryDelta, ConfigDocument

# Kategorie -> Identitätsfeld für Listen-Kategorien (der Name-artige
# Schlüssel, über den zwei Einträge als "dasselbe Objekt" erkannt werden,
# 7.5) - `sensor_config`/`federation_config` sind Singletons, kein
# Namensabgleich nötig.
_LIST_IDENTITY_FIELD = {
    "object_types": "name",
    "workflows": "name",
    "dmn_definitions": "name",
    "roles": "name",
    "approval_config": "action_type",
}
SINGLETON_CATEGORIES = ("sensor_config", "federation_config")


def normalize(value: str, pattern: str | None) -> str:
    """7.5: die Ignore-Regex wird vor dem Namensabgleich auf beide Namen
    angewendet (z. B. um numerische Präfixe zu entfernen) - passende
    Teilstrings werden entfernt, nicht ersetzt."""
    if not pattern:
        return value
    return re.sub(pattern, "", value)


def resolve_pattern(category: str, ignore_regex: dict[str, str] | None) -> str | None:
    if not ignore_regex:
        return None
    return ignore_regex.get(category) or ignore_regex.get("*")


def _diff_fields(
    base: dict[str, Any], compare: dict[str, Any], *, exclude: set[str]
) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for key in sorted(set(base) | set(compare)):
        if key in exclude:
            continue
        if base.get(key) != compare.get(key):
            fields[key] = {"base": base.get(key), "compare": compare.get(key)}
    return fields


def diff_list_category(
    category: str,
    base_items: list[dict[str, Any]] | None,
    compare_items: list[dict[str, Any]] | None,
    *,
    ignore_regex: dict[str, str] | None,
) -> CategoryDelta:
    identity_field = _LIST_IDENTITY_FIELD[category]
    pattern = resolve_pattern(category, ignore_regex)
    base_by_norm = {normalize(item[identity_field], pattern): item for item in (base_items or [])}
    compare_by_norm = {
        normalize(item[identity_field], pattern): item for item in (compare_items or [])
    }

    delta = CategoryDelta()
    for norm_name in sorted(set(base_by_norm) - set(compare_by_norm)):
        delta.only_in_base.append(base_by_norm[norm_name][identity_field])
    for norm_name in sorted(set(compare_by_norm) - set(base_by_norm)):
        delta.only_in_compare.append(compare_by_norm[norm_name][identity_field])
    for norm_name in sorted(set(base_by_norm) & set(compare_by_norm)):
        base_item = base_by_norm[norm_name]
        compare_item = compare_by_norm[norm_name]
        # Anzeigename bewusst der Basisinstanz-Rohwert - die Ignore-Regex
        # betrifft nur die Zuordnung, nicht die Darstellung (7.5).
        display_name = base_item[identity_field]
        fields = _diff_fields(base_item, compare_item, exclude={identity_field})
        if fields:
            delta.differing[display_name] = fields
        else:
            delta.identical.append(display_name)
    return delta


def diff_singleton_category(
    category: str, base_doc: dict[str, Any] | None, compare_doc: dict[str, Any] | None
) -> CategoryDelta:
    delta = CategoryDelta()
    if base_doc is None and compare_doc is None:
        return delta
    if base_doc is None:
        delta.only_in_compare.append(category)
        return delta
    if compare_doc is None:
        delta.only_in_base.append(category)
        return delta
    fields = _diff_fields(base_doc, compare_doc, exclude=set())
    if fields:
        delta.differing[category] = fields
    else:
        delta.identical.append(category)
    return delta


def compare_documents(
    base: ConfigDocument,
    compare: ConfigDocument,
    *,
    categories: set[str],
    ignore_regex: dict[str, str] | None = None,
) -> dict[str, CategoryDelta]:
    result: dict[str, CategoryDelta] = {}
    for category in sorted(categories):
        if category in _LIST_IDENTITY_FIELD:
            base_items = getattr(base, category)
            compare_items = getattr(compare, category)
            result[category] = diff_list_category(
                category,
                [item.model_dump() for item in base_items] if base_items else None,
                [item.model_dump() for item in compare_items] if compare_items else None,
                ignore_regex=ignore_regex,
            )
        elif category in SINGLETON_CATEGORIES:
            base_doc = getattr(base, category)
            compare_doc = getattr(compare, category)
            result[category] = diff_singleton_category(
                category,
                base_doc.model_dump() if base_doc else None,
                compare_doc.model_dump() if compare_doc else None,
            )
    return result
