# 0013 — Enforced object hierarchy: object_type_id/root flag instead of a resolved name across the service boundary

**Status:** accepted
**Context:** Concept 2.2a, Session P5b-S1

## Decision

The enforced object hierarchy ("folder/document class X may only sit under parent class Y", 2.2a) is implemented as follows:

- `allowed_parent_types: list[str] | None` (Object-Type Service) lists the names of permitted parent folder classes, with the sentinel `"$ROOT"` for "directly under the root". Empty/`None` = placeable anywhere (backward compatibility with all pre-existing object types).
- The actual check stays in `dms-constraint-engine` (a new function, integrated into `validate()`) — **not a new service**, consistent with ADR 0003 (constraint engine as a shared library rather than a standalone service).
- **Callers (Folder/Document Service) pass `parent_object_type_id: int | None` + `parent_is_root: bool` to `POST /object-types/{id}/validate`, not the already-resolved name of the parent class.** The Object-Type Service itself resolves `parent_object_type_id` to a name (its own DB query).

## Rationale

- **The Object-Type Service remains the single source of truth for object type names.** The Folder Service and Document Service only know their respective parent folder's `object_type_id` anyway (an opaque reference, as with every other cross-service relationship in this system, 3.1) — otherwise they would have to insert an additional `GET /object-types/{id}` call themselves, just to resolve the name before sending it right back to the very service that already knows it. One fewer roundtrip per placement check.
- **`"$ROOT"` is deliberately a separate flag (`parent_is_root`), not derived from "parent folder has no object type".** The root itself has `object_type_id = None` (it can never carry its own class, see `folder-service`), but that is not the same as an ordinary, simply untyped intermediate folder — without the explicit distinction, a class with `allowedParentTypes: ["$ROOT"]` could wrongly also be placed under any arbitrary untyped folder. A parent folder without its own object type therefore satisfies **no** `allowedParentTypes` requirement that names a concrete type or `"$ROOT"` — a deliberate decision, captured in `dms-constraint-engine`'s tests.
- **Only folder classes (`applies_to == "folder"`) may be referenced.** Only folders can be parent objects (2.1) — an `allowedParentTypes` entry pointing to a document class would be impossible to satisfy and is therefore already rejected with `422` at object-type creation/modification time (Object-Type Service, `repository._validate_allowed_parent_types`), rather than only surfacing at the first actual placement attempt.
- **`icon` is only permitted for folder classes** (also `422` on violation) — per Concept 2.2a, document classes have no icon display planned (which in the Explorer would only make sense for folders anyway).
- **Enforcement on both creation *and* moving of folders, but only on creation of documents** — documents have no move operation (deliberately immutable `folder_id`, see `docs/services/document-service.md`), whereas folders do (`PATCH /folders/{id}` with `parent_id`, present since P3-S3, but never re-validated against object-type constraints until now).
- **No retroactive check of existing placements** when a class definition is tightened after the fact (e.g. `allowedParentTypes` is added after many instances already exist) — consistent with this system's general principle of checking constraints only on write operations, not via a background job sweeping the whole dataset (comparable to object-type attribute validation itself, which likewise only affects future writes when tightened retroactively). Recorded as an open point in Concept 13.
- **No cycle detection across multiple classes** (e.g. A only allows B as a parent class, B only allows A) — a full reachability check up to the root would be overengineering for the current scope; a broken, never-satisfiable configuration surfaces at the latest at the first failed placement attempt (no silent failure state, just no creation-time check).

## Consequences

- Object-Type Service, Folder Service, and Document Service change; Permission Service, Storage Service, Search Service, etc. are unaffected (none of these services know about object types).
- `ObjectTypeClient.validate()` (identical code in `folder-service` and `document-service`, as before this session) gains two new optional parameters (`parent_object_type_id`, `parent_is_root`) — default values keep the signature backward-compatible for callers without placement context.
- `FolderClient` in `document-service` was extended from `exists(folder_id) -> bool` to `get(folder_id) -> dict | None` (now returns the full folder body including `object_type_id`, instead of discarding everything but a boolean) — the only existing caller (`create_document`) was adjusted accordingly.
- The admin-side GUI for setting `allowedParentTypes`/`icon` follows only with P5b-S3 (GUI object-type/layout designer) — this session covers exclusively the backend data model and enforcement, verified directly via the API (curl/pytest), not via the admin UI.
