"""Documents measured against the filing structure (P91-S2b, ADR 0255).

Pure, like folder-service's counterpart: the caller loads the documents,
their folders' types (one folder-service call) and object-type-service's
rules once."""

from dms_constraint_engine import (
    ROOT_PARENT_TYPE,
    missing_required_attributes,
    parent_rules_from_type,
    placement_errors,
    root_rules_from_config,
)

# The reference number is assigned by the system, never a missing input.
_SYSTEM_ATTRIBUTES = {"Kennzeichen"}
_UNSET = object()


def evaluate_document(
    document: dict,
    folder_types: dict[str, int | None],
    types_by_id: dict[int, dict],
    structure_config: dict,
    *,
    object_type_id=_UNSET,
) -> list[dict]:
    type_id = document["object_type_id"] if object_type_id is _UNSET else object_type_id
    problems: list[dict] = []
    own_type = types_by_id.get(type_id) if type_id is not None else None
    if type_id is not None and own_type is None:
        problems.append({"kind": "unknown_type", "messages": [f"Objekttyp {type_id} unbekannt"]})

    folder_id = document["folder_id"]
    if folder_id in (None, "root"):
        parent_rules = root_rules_from_config(structure_config)
        parent_type_name: str | None = ROOT_PARENT_TYPE
    else:
        parent_type = types_by_id.get(folder_types.get(folder_id))
        parent_rules = parent_rules_from_type(parent_type)
        parent_type_name = parent_type["name"] if parent_type else None
    messages = placement_errors("document", own_type, parent_rules, parent_type_name)
    if messages:
        problems.append({"kind": "placement", "messages": messages})

    if own_type is not None:
        definitions = [
            d for d in own_type.get("attributes") or [] if d.get("name") not in _SYSTEM_ATTRIBUTES
        ]
        missing = missing_required_attributes(definitions, document.get("attributes") or {})
        if missing:
            problems.append(
                {
                    "kind": "missing_required",
                    "messages": [f"Pflichtangaben fehlen: {', '.join(missing)}"],
                }
            )
    return problems


def document_report(
    documents: list[dict],
    folder_types: dict[str, int | None],
    types_by_id: dict[int, dict],
    structure_config: dict,
    *,
    limit: int,
) -> dict:
    items: list[dict] = []
    counts = {"placement": 0, "missing_required": 0, "unknown_type": 0}
    nonconforming = 0
    for document in documents:
        problems = evaluate_document(document, folder_types, types_by_id, structure_config)
        if not problems:
            continue
        nonconforming += 1
        for problem in problems:
            counts[problem["kind"]] += 1
        if len(items) < limit:
            own_type = types_by_id.get(document["object_type_id"])
            items.append(
                {
                    "id": document["id"],
                    "title": document["title"],
                    "folder_id": document["folder_id"],
                    "object_type": own_type["name"] if own_type else None,
                    "problems": problems,
                }
            )
    return {
        "checked": len(documents),
        "nonconforming": nonconforming,
        "counts": counts,
        "items": items,
        "truncated": nonconforming > len(items),
    }
