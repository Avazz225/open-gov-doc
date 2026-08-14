"""Delta/comparison function between two configuration exports (7.5,
P14-S1) - purely read-only/diagnostic, does not change anything on either
side (see `docs/adr/0040-config-compare-field-level-diff-no-cross-
installation-fetch.md` for the architectural decisions: field-level instead
of deep diff per nested list, no automatic cross-installation
fetch in this session)."""

import re
from typing import Any

from config_service.schemas import CategoryDelta, ConfigDocument

# Category -> identity field for list categories (the name-like
# key by which two entries are recognized as "the same object",
# 7.5) - `sensor_config`/`federation_config` are singletons, no
# name matching needed.
_LIST_IDENTITY_FIELD = {
    "object_types": "name",
    "workflows": "name",
    "dmn_definitions": "name",
    "business_calendars": "name",
    "roles": "name",
    "approval_config": "action_type",
}
SINGLETON_CATEGORIES = ("sensor_config", "federation_config")
# `realm_roles` is a plain name list (`list[str]`, not a
# `list[dict]` category like the others) - its own, simpler diff mode
# instead of `_LIST_IDENTITY_FIELD` (which expects `item[identity_field]` on
# every entry).
STRING_LIST_CATEGORIES = ("realm_roles",)


def normalize(value: str, pattern: str | None) -> str:
    """7.5: the ignore regex is applied to both names before the name
    matching (e.g. to strip numeric prefixes) - matching
    substrings are removed, not replaced."""
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
        # Display name is deliberately the base instance's raw value - the
        # ignore regex only affects matching, not display (7.5).
        display_name = base_item[identity_field]
        fields = _diff_fields(base_item, compare_item, exclude={identity_field})
        if fields:
            delta.differing[display_name] = fields
        else:
            delta.identical.append(display_name)
    return delta


def diff_string_list_category(
    category: str,
    base_names: list[str] | None,
    compare_names: list[str] | None,
    *,
    ignore_regex: dict[str, str] | None,
) -> CategoryDelta:
    pattern = resolve_pattern(category, ignore_regex)
    base_by_norm = {normalize(name, pattern): name for name in (base_names or [])}
    compare_by_norm = {normalize(name, pattern): name for name in (compare_names or [])}

    delta = CategoryDelta()
    for norm_name in sorted(set(base_by_norm) - set(compare_by_norm)):
        delta.only_in_base.append(base_by_norm[norm_name])
    for norm_name in sorted(set(compare_by_norm) - set(base_by_norm)):
        delta.only_in_compare.append(compare_by_norm[norm_name])
    for norm_name in sorted(set(base_by_norm) & set(compare_by_norm)):
        # A name is either identical or not present at all - unlike
        # `diff_list_category`, there are no further fields that could
        # differ.
        delta.identical.append(base_by_norm[norm_name])
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
        elif category in STRING_LIST_CATEGORIES:
            result[category] = diff_string_list_category(
                category,
                getattr(base, category),
                getattr(compare, category),
                ignore_regex=ignore_regex,
            )
    return result
