# 0255 — Enforced filing structure (scoping)

**Status:** accepted (scoping; built in P91-S2/S3)
**Context:** P91-S1. User request 2026-11-21: admins unify the filing structure organisation-wide and
enforce it. What kind of folder can be created comes from its parent, for example
root → Abteilung → Team → Vorgang → Akten (documents). Structures can come from templates, and creation
can be restricted to templates.

## What exists

- **Child-side rules:** every object type (folder *and* document) can declare `allowed_parent_types`,
  with `$ROOT` for the top. `dms_constraint_engine._validate_parent` checks it:
  - on folder create and move;
  - on document create and move;
  - on template apply (pre-checked for the whole tree before anything is created, P79-S1).
- **Gaps:**
  - An untyped folder or document bypasses every rule: `_validate_against_object_type` returns early
    without a type.
  - A type without `allowed_parent_types` may go anywhere.
  - Nothing on the parent side says "only these children here".
  - The root accepts anything.
- **The eGov package** already models a chain this way: Abteilung ← `$ROOT`, Aktenplan ← Abteilung,
  Akte (document types) ← Aktenplan, Handakte/Arbeitsvorrat ← `$ROOT`/Abteilung.
- **Structure templates** (ADR 0056, folder-service): a JSON tree of name and type per node, applied
  below a target.
  - Create, apply and delete are ungated today.
  - The source folder is chosen by typed-in id.
  - Templates aren't a config category.
- **Folders get no type change:** `PATCH /folders` can rename and move, not retype.
- **Who creates folders and documents:**
  - user-ui;
  - teamspace-service (teamspace roots under root);
  - the connector SDK's `DmsTreeClient`, used by WebDAV, CMIS and migration's inbound push (usually
    untyped);
  - mail-connector (documents into a chosen folder);
  - archival-service (restore);
  - document-service itself (quarantine release, redaction copies, conflict copies).

## Decision 1 — Rule model: parent-side lists that only narrow

**Options:**
- **(a) Child side only,** with the parent view derived in the UI. It can't express "nothing untyped
  here", or "only these classes" for types that have no `allowed_parent_types`.
- **(b) Parent side only,** migrating `allowed_parent_types` away. That breaks the eGov package, config
  exports and existing installations, for no gain.
- **(c) Both, and both must pass.**

**Chosen: (c).** Folder classes get two optional lists:
- `allowed_child_folder_types`: names of folder classes;
- `allowed_document_types`: names of document types.

**Semantics:**
- `null` means open (today's behaviour). A list means *only* these typed children; **untyped children
  are refused** below such a parent. An empty list means none.
- A child is accepted when its own `allowed_parent_types` admits the parent *and* the parent's list
  admits the child. **A rule can only narrow, never widen.** No existing placement that is allowed today
  becomes allowed by accident, and nothing changes until an admin sets a list.
- **The root** gets the same two lists in a new singleton `structure_config` in object-type-service, the
  counterpart of `$ROOT` on the child side.
- **One computation:**
  - `dms_constraint_engine` gets a pure `allowed_children(parent_type, all_types, root_config)` and a
    `validate_child(parent_schema, child_type_name)`.
  - object-type-service exposes `GET /structure/allowed-children?parent_type_id=` (or `root`), which
    returns folder classes and document types.
  - folder-service, document-service and both UIs use only this, so no component re-derives the rules.
- **Contradictions are shown, not forbidden:**
  - a listed child whose own `allowed_parent_types` excludes the parent is unreachable;
  - a class that no list admits is orphaned.
  - The admin UI flags both.

## Decision 2 — Strictness: per folder class, plus an installation-wide rollout mode

There is no single "structure mandatory" switch. Strictness emerges from configuration:
- the root lists: what may sit at the top;
- per folder class: the two lists;
- per folder class, **`children_from_template_only`:** new child folders under this class can only be
  created by applying a template (for example: under "Team", a "Vorgang" only from the "Vorgangsakte"
  template, which brings its subfolders). Documents are not affected; they are uploaded, not templated.

An installation-wide **`structure_enforcement: off | report | enforce`** in `structure_config` (default
`off` on upgrade):
- **`report`:** every check runs, nothing is refused. Violations are logged and counted (a sensor
  metric), and they show in the conformity report. This is how an organisation rolls a structure out
  without stopping work.
- **`enforce`:** violations are refused.
- **`off`:** only today's child-side rules apply.

**Exempt areas,** so organisational features keep working:
- teamspace roots: created by teamspace-service, they are areas, not filing structure;
- the protected special folders (root's inbox and outbox);
- below an exempt or untyped area root, a typed folder's own lists apply as usual. Teamspaces stay open
  unless their folders are typed into a structure.

## Decision 3 — Enforcement points

The checks live where the data is owned: folder-service for folders, document-service for documents.
Every client goes through them, so clients need nothing beyond showing the error well.

| Path | Check | On violation |
|---|---|---|
| `POST /folders` | parent lists (or root config), template-only flag, the child's own rule | `422` naming the allowed classes |
| `PATCH /folders` move | the same against the new parent; descendants are not re-checked (their relation to the moved folder is unchanged) | `422` |
| Template apply | every node against its parent's lists (pre-check for the whole tree, as today); `children_from_template_only` is satisfied by definition | `422` with the node path, nothing created |
| `POST /documents`, move | `allowed_document_types` of the folder's class (untyped documents refused below a list) | `422` |
| New version, metadata change | none: placement doesn't change | — |
| WebDAV `MKCOL`/`PUT`, CMIS `createFolder`/`createDocument`, migration inbound | through `DmsTreeClient`: see "Defaults" below; otherwise the `422` mapped to the protocol (WebDAV `403` with the message, CMIS `constraint`) | refused |
| mail-connector assignment | the document check; a refusal is shown per attachment in the Poststelle, which picks another folder or a type | refused per attachment |
| teamspace roots | exempt (Decision 2) | — |
| Restore (archival, trash), quarantine release, redaction and conflict copies | exempt: they restore or derive existing data, and a rule changed later must not make that impossible. Logged with the reason | — |
| Configuration import | imports rules, not folders, so nothing to refuse. The import result says how many existing folders the new rules would make non-conforming (from the report) | — |

**Defaults for untyped creators.** A folder class may name a `default_child_folder_type` and a
`default_document_type`.
- When a client creates without a type below such a class, folder-service and document-service use the
  default.
- Required attributes are not demanded for a defaulted creation. The object is created with
  "Pflichtangaben fehlen", like a template-applied folder (ADR 0056), and shows in the report until
  filled.
- Without a default the creation is refused. This lets WebDAV and CMIS work inside a strict structure
  without knowing about types.

## Decision 4 — Existing data: grandfathered, reported, fixable

- **Nothing existing is blocked:** reading, editing, versioning and moving *out* always work.
  Non-conforming folders keep their place.
- **New children under a non-conforming folder follow its own class's rules** as usual. An untyped
  legacy folder has no class and so no lists: new children below it are accepted (subject to their own
  child-side rules), so legacy subtrees stay usable until they are retyped.
- **Conformity report (admin):**
  - folder-service walks the tree against the rules: folders in the wrong place, untyped folders below
    a list, and missing required attributes from defaults or templates;
  - document-service reports documents per folder against `allowed_document_types`;
  - admin-ui merges both, grouped by rule, with paths and counts, exportable as CSV.
- **The way to conformity:** a new `POST /folders/retype` (admin, bulk), which sets the type of
  selected folders. It is checked like a create (placement, lists), and each failure is listed.
  - Documents can't change their type today (`PATCH /documents` has no `object_type_id`). The build adds
    the same bulk retype for documents (`POST /documents/retype`, admin), checked like an upload. Fully
    typing an untyped document estate is otherwise impractical.
  - Moving stays the main tool for misplaced objects.

## Decision 5 — Templates become admin-managed configuration

- **Who:**
  - creating, editing and deleting templates needs `admin.object_config` (today ungated);
  - applying needs `folder.write` on the target, as now.
  - Existing user-created templates stay and are listed for admins to keep or delete.
- **Building:** capture from a folder with a tree picker (instead of the typed-in id), and a template
  editor in admin-ui (add, rename and type nodes). Nodes are checked against the rules when saved, so a
  template can't contain a structure the rules refuse.
- **Where offered:** user-ui offers "Struktur aus Vorlage anlegen" in a folder's context menu only for
  templates whose root node is allowed under that folder (the same `allowed-children` computation). Under
  a `children_from_template_only` class it is the only way to create subfolders, and "Neuer Ordner" says
  so.
- **Configuration:**
  - templates become a config-service category `folder_templates`, owned by folder-service and
    exported/imported through its endpoints, matched by name.
  - ADR 0056 kept *folder instances* out of configuration; a template is a definition, so this doesn't
    contradict it.
  - **The eGov package** gains the parent-side lists and a template "Aktenplan-Grundgerüst" (Abteilung →
    Aktenplan).

## Decision 6 — What the user sees (built in P91-S3)

- **"Neuer Ordner":** a class select with only the allowed classes (one allowed means preselected,
  none means the button is disabled with the reason). Template-only classes link to "Aus Vorlage".
- **Upload:** only the allowed document types; the folder's default is preselected.
- **Errors in plain German,** naming what is allowed: "Unter „Team“ sind nur Ordner der Klasse
  „Vorgang“ erlaubt."
- **admin-ui, object types:**
  - per folder class: the two lists, the defaults and the template-only flag;
  - a structure view: the tree of classes from root, derived from both sides, with contradiction and
    orphan markers;
  - the enforcement mode, the conformity report, and template management.

## Not in scope

- Rules per subtree instance (a "Team A" folder stricter than "Team B" of the same class). The class is
  the unit; a different rule means a different class.
- Depth limits and name patterns per level: names are already covered by `naming_constraints`.
- Retroactively moving or retyping data automatically. The report and the bulk retype help, but humans
  decide.
- Hiding what the caller can't read: that is P91-S4.

## Consequences

- **object-type-service:**
  - folder-class fields `allowed_child_folder_types`, `allowed_document_types`,
    `default_child_folder_type`, `default_document_type`, `children_from_template_only`;
  - the `structure_config` singleton with root lists and enforcement mode;
  - `GET /structure/allowed-children`;
  - validation: referenced names must exist and have the right `applies_to`.
- **dms-constraint-engine:** `allowed_children` and `validate_child` (pure).
- **folder-service:** the checks, defaults, exemptions, report, bulk retype, template gating and
  editing, and the template config endpoints.
- **document-service:** the checks, defaults, exemptions, its part of the report, and the bulk retype.
- **config-service:** the new object-type fields, `structure_config`, and the `folder_templates`
  category.
- **Connector SDK and connectors:** map the `422` to protocol errors.
- **mail-connector:** shows refusals per attachment.
- **The eGov package:** lists and a template.
- **Sensors:** a violation counter, which is useful in `report` mode.

## Implementation notes (P91-S2a)

- **One check endpoint.** folder- and document-service ask object-type-service's `POST /structure/check`
  once per create or move. It answers the mode, the errors and, for an untyped child, an admitted
  default. The rules are evaluated only there, with the pure `dms_constraint_engine.validate_child`.
- **Updates keep what isn't sent.** `PUT /object-types` changes the structure fields only when they are
  in the request (`model_fields_set`), so the current admin-ui, which doesn't know them yet, can't reset
  a structure.
- **Exemptions by principal:** teamspace roots are recognised by the principal `teamspace-service`
  creating under the root, and restores by `archival-service`. Quarantine release and redaction copies
  never enable the check.
- **Upload path:** document-service caches the `off` answer for 15 s, so an installation without a
  structure pays no extra request per upload.
- **Configuration import in two passes.** Missing types are created without the fields that name other
  types, then every entry's full definition is applied. The structure lists create cycles (Team →
  Vorgang → `allowed_parent_types: [Team]`) that no entry order could import in one pass.
- **Connectors:** the SDK raises `StructureRuleError` on `422`. WebDAV answers `403` with the reason,
  CMIS a `constraint` exception, and mail-connector keeps what was filed and names the refused
  attachment.

## Implementation notes (P91-S2b)

- **Report and retype evaluate in memory.** The rules are loaded once and evaluated with pure engine
  helpers (`placement_errors`, `missing_required_attributes`, `parent_rules_from_type`,
  `root_rules_from_config`), so a report over thousands of folders costs two requests.
  document-service gets its folders' types in one batch (`POST /folders/types`). On the dev stack
  (1,822 folders, 2,258 documents) both answer at once; the first run found 52 folders and 23
  documents whose type had been deleted.
- **Retyping is mode-independent:** it is the tool for reaching conformity, so it never creates a
  misplacement even while the mode is `off`.
- **Teamspace roots** carry `structure_exempt`. teamspace-service confirms existing ones in a
  background task with retries; a one-off call at startup failed when both services restarted together.
- **Templates in configuration** reference folder classes by name.
- **The eGov package** (1.4.0) ships the parent-side lists and a template, but not the mode.

## Implementation notes (P91-S3)

- **One source for forms.** Both UIs use object-type-service's `GET /structure/allowed-children`. The
  admin overview draws the same two-sided rule client-side, only for display.
- **Loading is not "everything allowed".** user-ui's hook tells "loading" from "rules unreadable". The
  template dialog briefly offered non-fitting templates until it waited for the rules (found in the e2e
  screenshot).
- **user-ui's "Vorlagen" pane keeps its typed-in folder ids** (capture for admins, apply for everyone).
  The guided paths are the admin page's folder picker and the folder context menu; replacing the pane's
  id fields is left for later.
