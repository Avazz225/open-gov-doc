"""Folders measured against the filing structure (P91-S2b, ADR 0255).

Pure: the caller loads the folders and object-type-service's rules once, so a
conformity report over a whole tree costs two requests, not one per folder.
Used by `GET /structure/conformity`, by bulk retyping (the same check for the
folder with its new type) and by template validation."""

from dms_constraint_engine import (
    ROOT_PARENT_TYPE,
    missing_required_attributes,
    parent_rules_from_type,
    placement_errors,
    root_rules_from_config,
)

from folder_service.settings import PROTECTED_FOLDER_IDS, ROOT_FOLDER_ID

_UNSET = object()


def folder_path(folder_id: str, by_id: dict[str, dict]) -> str:
    """ "/Bauamt/Team Hochbau/Bauantrag 12" - root excluded, cycles guarded."""
    names: list[str] = []
    seen: set[str] = set()
    current = by_id.get(folder_id)
    while current is not None and current["id"] != ROOT_FOLDER_ID and current["id"] not in seen:
        seen.add(current["id"])
        names.append(current["name"])
        current = by_id.get(current["parent_id"]) if current["parent_id"] else None
    return "/" + "/".join(reversed(names))


def evaluate_folder(
    folder: dict,
    by_id: dict[str, dict],
    types_by_id: dict[int, dict],
    structure_config: dict,
    *,
    object_type_id=_UNSET,
) -> list[dict]:
    """Problems of one folder in its current place. `object_type_id`
    evaluates it as if it had that type (bulk retyping)."""
    type_id = folder["object_type_id"] if object_type_id is _UNSET else object_type_id
    problems: list[dict] = []
    own_type = types_by_id.get(type_id) if type_id is not None else None
    if type_id is not None and own_type is None:
        problems.append({"kind": "unknown_type", "messages": [f"Objekttyp {type_id} unbekannt"]})

    parent_id = folder["parent_id"]
    at_root = parent_id == ROOT_FOLDER_ID
    exempt_area_root = at_root and folder.get("structure_exempt")
    if not exempt_area_root:
        if at_root:
            parent_rules = root_rules_from_config(structure_config)
            parent_type_name: str | None = ROOT_PARENT_TYPE
        else:
            parent = by_id.get(parent_id)
            parent_type = (
                types_by_id.get(parent["object_type_id"])
                if parent and parent["object_type_id"] is not None
                else None
            )
            parent_rules = parent_rules_from_type(parent_type)
            parent_type_name = parent_type["name"] if parent_type else None
        messages = placement_errors("folder", own_type, parent_rules, parent_type_name)
        if messages:
            problems.append({"kind": "placement", "messages": messages})

    if own_type is not None:
        missing = missing_required_attributes(
            own_type.get("attributes") or [], folder.get("attributes") or {}
        )
        if missing:
            problems.append(
                {
                    "kind": "missing_required",
                    "messages": [f"Pflichtangaben fehlen: {', '.join(missing)}"],
                }
            )
    return problems


def folder_report(
    folders: list[dict], types_by_id: dict[int, dict], structure_config: dict, *, limit: int
) -> dict:
    """Every active folder except the root and the special folders. Items
    are capped at `limit`; the counts always cover everything."""
    by_id = {folder["id"]: folder for folder in folders}
    items: list[dict] = []
    counts = {"placement": 0, "missing_required": 0, "unknown_type": 0}
    checked = 0
    nonconforming = 0
    for folder in folders:
        if folder["id"] in PROTECTED_FOLDER_IDS:
            continue
        checked += 1
        problems = evaluate_folder(folder, by_id, types_by_id, structure_config)
        if not problems:
            continue
        nonconforming += 1
        for problem in problems:
            counts[problem["kind"]] += 1
        if len(items) < limit:
            own_type = types_by_id.get(folder["object_type_id"])
            items.append(
                {
                    "id": folder["id"],
                    "name": folder["name"],
                    "path": folder_path(folder["id"], by_id),
                    "object_type": own_type["name"] if own_type else None,
                    "problems": problems,
                }
            )
    return {
        "checked": checked,
        "nonconforming": nonconforming,
        "counts": counts,
        "items": items,
        "truncated": nonconforming > len(items),
    }


def template_errors(node: dict, types_by_id: dict[int, dict], *, path: str = "") -> list[str]:
    """A template's own edges (P91-S2b): each node's type exists and is a
    folder class, and each child fits its parent node on both sides. The
    root node's fit depends on where the template is applied, which apply
    checks."""
    errors: list[str] = []
    here = f"{path}/{node['name']}"
    node_type = None
    if node.get("object_type_id") is not None:
        node_type = types_by_id.get(node["object_type_id"])
        if node_type is None:
            errors.append(f"{here}: Objekttyp {node['object_type_id']} unbekannt")
        elif node_type.get("applies_to") != "folder":
            errors.append(f"{here}: „{node_type['name']}“ ist keine Ordnerklasse")
            node_type = None
    rules = parent_rules_from_type(node_type)
    for child in node.get("children") or []:
        child_type = (
            types_by_id.get(child.get("object_type_id")) if child.get("object_type_id") else None
        )
        messages = placement_errors(
            "folder", child_type, rules, node_type["name"] if node_type else None
        )
        if messages:
            errors.append(f"{here}/{child['name']}: " + "; ".join(messages))
        errors.extend(template_errors(child, types_by_id, path=here))
    return errors
