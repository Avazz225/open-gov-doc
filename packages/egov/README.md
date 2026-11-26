# eGov Configuration Package

First configuration package maintained by the project itself (concept §14.2) — a sensible
default configuration for German public administration, so that a fresh installation
is directly usable as a ready-to-use eGov DMS after applying it, instead of having to
manually replicate each of the following settings one by one. Technically, a package is nothing
other than an ordinary configuration document (§7.3) with an additional, purely descriptive `manifest`
(§14.1) — see [ADR 0058](../../docs/adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)
for the format, [ADR 0059](../../docs/adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md)
for Part 1, and [ADR 0060](../../docs/adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)
for Part 2 of this specific package.

## Applying

- **Admin UI** (recommended): `/config-packages/` → select `config.json` → preview → apply.
- **CLI**: `dms config import packages/egov/config.json` (uses the same, already existing
  configuration import as any other 7.3 export).
- **Fleet Management**: `POST /installations/{id}/provision` with the contents of this file as the body
  (centralized initial provisioning of multiple installations, see `docs/services/fleet-management-service.md`).

Additive/upsert, repeatably applicable (§14.1) — can also be applied to an already running,
partially differently configured installation; no initial-setup requirement.

## Aktenplan Object-Type Hierarchy (Part 1, P17-S2; extended Phase 80 Session 1)

Contains the `object_types` category:

| Object Type | Type | Parent | Attributes | Reference-Number Format | Retention → Archive | VS Classification | Min. Signature | Deletion Reason |
|---|---|---|---|---|---|---|---|---|
| `Abteilung` | Folder | `$ROOT` | — | — | — | — | — | — |
| `Aktenplan` | Folder | `Abteilung` | — | — | — | — | — | — |
| `Handakte` | Folder | `$ROOT`/`Abteilung` | — | — | — | — | — | — |
| `Arbeitsvorrat` | Folder | `$ROOT`/`Abteilung` | — | — | — | — | — | — |
| `Akte` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10y → +1y (3653 → 4018d) | — | — | — |
| `Verschlusssache-Akte` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10y → +1y (3653 → 4018d) | VS-NfD | AES | required |
| `Verschlusssache-Akte-Vertraulich` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 15y → +1y (5478 → 5843d) | VS-VERTRAULICH | AES | required |
| `Verschlusssache-Akte-Geheim` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 20y → +1y (7305 → 7670d) | GEHEIM | QES | required |
| `Verschlusssache-Akte-Streng-Geheim` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 30y → +1y (10958 → 11323d) | STRENG GEHEIM | QES | required |

\* Required attribute. The reference-number generation itself (`{Laufende_Nummer}`, year-based
reset) is the already existing reference-number generator (P5e sessions) — `{Federführung}` is a
newly supported, **attribute-based** placeholder since P17-S2 (any placeholder that is not a
date/counter placeholder is interpreted as an attribute name), a direct implementation of the
concept example `{Abteilung}-{YYYY}-{Laufende_Nummer}` — here deliberately using the actually
more sensible `Federführung`, which is already a required attribute, instead of a redundant
second "Abteilung" attribute (the Akte is structurally already located under an
`Abteilung` folder instance anyway).

`Handakte`/`Arbeitsvorrat` (hand folders/work trays, 14.2) were added at P31-S7 — documented here
retroactively (Phase 80 Session 1) after being found to have silently drifted out of sync with this
README and the manifest version, despite ADR 0060 itself naming "future changes will regularly
increment the version" as the expectation.

**All four VS classification levels the concept names are now demonstrated** (Phase 80 Session 1,
previously only `VS-NfD`) — with a **plausible minimum signature level and mandatory deletion
justification per tier**, escalating with sensitivity (AES for VS-NfD/VS-VERTRAULICH, QES for the two
most sensitive tiers), and **retention/archival periods now differentiated by tier** instead of one
flat 10-year value for every document class — regular `Akte`s keep the original 10 years, classified
ones scale from 10 (VS-NfD) up to 30 years (STRENG GEHEIM), each archived one year after its own
retention period expires (Konzept 5.6, an administrative buffer for the disposal review itself, not a
legal requirement). **All of these remain changeable defaults, not a system requirement** — the
concrete legal deadline/classification/signature policy remains the responsibility of the installation
per federal state/legal domain (concept text, verbatim, same framing as the original P17-S2 session).

## Mailroom, Process Templates, Four-Eyes, Business Calendar, Admin Roles (Part 2, P17-S3)

Extends the same `config.json` (14.1: additive/upsert, no new file) with the remaining five
components mentioned in §14.2 — `manifest.version` was `1.0.0` as of this session, the first
complete version of the package (now `1.1.0`, see Part 3 below). Details/rationale: see
[ADR 0060](../../docs/adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md).

| Category | Content |
|---|---|
| `realm_roles` | `dms-poststelle` — Keycloak realm role for incoming/outgoing mail (2.5), already enforced by `mail-connector` (since P15-S3), packaged here for the first time. |
| `workflows` | Three BPMN process templates for the Umlaufmappe (circulation folder) pattern (2.3/7.1): `egov_freigabe` (approve/reject decision via `exclusiveGateway`), `egov_kenntnisnahme` and `egov_aufgabe` (each a linear `manualTask`). Source XML is additionally located under [`workflows/`](workflows/) for easier maintenance/diff readability. |
| `approval_config` | Four-eyes default (4.3) for the sensitive action types named in 14.2 — `document.force_delete`, `folder.force_delete`, `document.force_unlock` (permanent deletion), `permission.role_assignment.create` (permission change, actually enforced since P17-S3), `config.import` (configuration import, actually enforced since P17-S3), since Phase 80 Session 1 `permission.role.create` (permission-*change*-family entry that had been missing — role *creation* itself, not just assigning an existing role, is the same sensitivity class), and since P89-S3 (1.3.0) `permission.role.update` plus the five AD-group mapping actions (see Part 5) — each with `requires_approval: true`. |
| `business_calendars` | `DE-Bund` (default, nine nationwide holidays) plus 16 state calendars `DE-BW` … `DE-TH` (each including federal holidays), real dates for 2026/2027. |
| `roles` | `Registratur/Aktenverwaltung` (`case.read`/`.write`, `document.read`/`.write`, `folder.read`/`.write`) and `Amtsleitung` (the same plus `scope_lock.bypass`) — extended, domain-separated admin roles on top of the technical `domain-admin-*` system roles (4.6). |

The four-eyes default requires that the approving person **not** be identical to the
initiating person (4.3) — after applying this package, permanent deletion, role creation/
assignment, and configuration import therefore generally require a second person before they
take effect (`POST /approval-requests/{id}/approve`).

## Config-Only Completions & Manifest-Drift Fix (Part 3, Phase 80 Session 1)

A dedicated research pass verified this package component-by-component against Konzept §14.2's own
9-item checklist and found it drifted from its own manifest (two object types, `Handakte`/`Arbeitsvorrat`,
had silently been added at P31-S7 with no version bump or README update) plus three areas that were
present but thin. This session closed all of it, config-only, no backend changes:

1. **`permission.role.create` added to `approval_config`** — the same "permission change" family as the
   already-covered `permission.role_assignment.create`, previously missing.
2. **All four VS classification levels now demonstrated** (previously only `VS-NfD`) — see the object-type
   table above for the three new `Verschlusssache-Akte-*` siblings.
3. **Retention/archival periods differentiated by file type** instead of one flat 10-year value for every
   document class (see the object-type table above).
4. **`required_signature_level`/`deletion_reason_required_override` set on every classified-documents
   object type** — both fields already existed in the exportable schema, simply unset in this package
   before now.
5. **Manifest drift fixed**: `manifest.version` bumped `1.0.0` → `1.1.0`, `manifest.description` updated,
   and this README's object-type table now lists `Handakte`/`Arbeitsvorrat`.

**Live-verified against the real running stack**: imported the full updated package via
`POST /config/import` — all categories applied with zero errors (9/9 object types, 3/3 workflows, 17/17
business calendars, 2/2 roles, 6/6 approval-config entries, the realm role idempotently confirmed
present), then inspected the resulting `object-type-service`/`permission-service` state directly to
confirm every new field round-tripped exactly as configured. Cleaned up afterward (this is a shared
development stack, not a real eGov installation) — deleted the object types/business calendars, reverted
`approval_config` back to its pre-import `false` defaults. Two residuals could not be cleaned up because
this platform has no delete endpoint for either resource type (by design — both are treated as
append-only/versioned): the 2 new roles and the 3 BPMN process definitions remain defined but unused
(no assignments, no running instances) on this particular dev stack.

## Deletion-Reason Catalog & Org-Unit → Role Linking (Part 4, Phase 80 Session 2)

Closes the two remaining structural gaps named in Part 3's own review: config-service previously had no
exportable category at all for the deletion-reason catalog or for connecting an org-unit group to a
role. New backend work in `config-service` first (three new `ConfigDocument` categories — see
[ADR 0233](../../docs/adr/0233-config-service-org-unit-role-and-deletion-reason-categories.md)), then
concrete eGov defaults using them:

| Category | Content |
|---|---|
| `deletion_reason_catalogs` | Four common German administrative deletion justifications: *"Aufbewahrungsfrist abgelaufen"*, *"Datenschutzrechtlicher Löschantrag"*, *"Vorgang irrtümlich angelegt"*, *"Gerichtliche Anordnung"*. Applied additively (merged into, not replacing, whatever an installation already curates) to both `document-service` and `folder-service` (ADR 0112 — the catalog is duplicated per-service). |
| `org_units` | Two org-unit groups (`Group.is_org_unit=True`, ADR 0143): `Amtsleitung` and `Registratur` — named identically to the two roles Part 2 already ships, so a real installation's HR/AD group sync can populate real members into these groups and have them automatically receive the matching role. |
| `org_unit_role_links` | Links each org-unit group to its identically-named role — every member of `Amtsleitung`/`Registratur` receives that role automatically via `permission-service`'s existing group-based RBAC resolution, no manual per-person role assignment needed after group membership is set. |

This is the first time this package connects its `Abteilung`/organizational object types to an actual
RBAC mechanism — previously the object-type hierarchy (Part 1) and the admin roles (Part 2) were two
unconnected islands, closed by request during Phase 80 Session 1's own review.

**Live-verified against the real running stack**: imported the full updated package — all 9 categories
applied with zero errors (2/2 org units, 2/2 org-unit→role links, catalog merged into both retention
configs), then confirmed directly that both `Amtsleitung`/`Registratur` groups exist with
`is_org_unit: true` and that `permission-service` holds a real `RoleAssignment(principal_type="group",
...)` row for each — i.e. that group membership genuinely grants the role, not just that the import
reported success. A real ordering bug was found and fixed during this verification: applying this
package's own `approval_config` entries before `org_unit_role_links` would have deferred the very links
this session added to a pending four-eyes approval instead of actually creating them (see ADR 0233's
"Consequences" section) — `config-service`'s dispatch order was corrected, and the org-unit→role links
apply, and were re-verified to apply, before that ordering fix would otherwise have silently swallowed
them. Cleaned up all created objects afterward (object types, business calendars, groups, role
assignments, catalog entries, approval-config); the two new roles remain as an accepted residual, same
reasoning as Part 3.

## Four-Eyes for Role Changes and AD Mapping, Output Stamping (Part 5, P89-S3, version 1.3.0)

**Four-eyes, completing Konzept §14.2 "permission change".** Until 1.2.0 the package gated *creating* a
role and *assigning* one, but not the other ways to change who gets which rights. 1.3.0 adds:

| Action type | What it changes |
|---|---|
| `permission.role.update` | An existing, already-assigned role's permissions — one change reaches everyone holding it |
| `auth.ad_group_role_mapping.create` / `.delete` | Which AD/Keycloak group grants which role |
| `auth.ad_group_role_composite_rule.create` / `.delete` | Roles granted only for a combination of groups |
| `auth.ad_group_mapping.default_role_set` | The role every user without a matching group gets |

The AD mapping decides rights for whole groups at login, so it is at least as sensitive as a single role
assignment.

**What stays ungated, and why:**
- **`permission.role_assignment.delete`:** taking a right away can't widen access. Under four-eyes,
  revoking access from someone who left would wait for a second person.
- **`permission.scope_lock.create`/`.release`:** a scope lock is itself a protective measure that is
  often urgent. The service gates it by its own capability.
- **`document.delete`/`folder.delete`:** these go to the trash and are recoverable. The final deletions
  (`force_delete`) are gated.
- **`object_type.update`, `workflow.process_definition.import`, `migration.transfer.start`:** these are
  configuration work, and frequent during setup. Importing a whole configuration is already gated
  (`config.import`).
- **`auth.superuser.activate`, `system.not_shutdown.trigger`, `document.classification.declassify`:**
  these carry their own approval rules from their services (break-glass approvers, VS declassification),
  independent of this package.

**Re-applying the package stays clean.** Since P89-S3 config-service skips an unchanged role instead of
writing it again. With `permission.role.update` gated, re-applying 1.3.0 therefore opens no approval
request for the package's own unchanged roles. A changed role is reported as "wartet auf Genehmigung"
with the request id, never silently counted as updated.

**Output stamping (`export_config`, the gap deferred in Part 4):**
- Every PDF export carries a QR code at the bottom right with the Aktenzeichen and the document id
  (`{kennzeichen} {document_id}`). So a printed copy can be matched to its record even when the document
  has no Aktenzeichen.
- The export history is appended after the document.
- Uses the new `export_config` category of config-service. config-service needs `admin.document_config`
  for it and grants itself that role at startup.

**Live-verified** against the running stack by importing only the two changed categories
(`approval_config`, `export_config`):
- all 12 approval entries and the export config applied without errors;
- the export afterwards showed the stamping defaults and all 12 action types gated.

Both were then put back to their previous values directly at permission-service/document-service. A
re-import would itself have needed four-eyes approval, since `config.import` was now gated.

## Filing Structure (Part 6, P91-S2b, version 1.4.0)

The parent side of the hierarchy ([ADR 0255](../../docs/adr/0255-enforced-filing-structure.md)):

| Folder class | Allowed child folders | Allowed documents | Default document type |
|---|---|---|---|
| `Abteilung` | `Aktenplan`, `Handakte`, `Arbeitsvorrat` | none | — |
| `Aktenplan` | none | the five `Akte` types | `Akte` |

Plus a structure template **"Aktenplan-Grundgerüst"**: Abteilung with Aktenplan, Handakte and
Arbeitsvorrat, folder classes referenced by name.

**The package deliberately doesn't set the enforcement mode or the root's lists** (`structure_config`).
- Those belong to the installation: importing the package must not suddenly start refusing uploads
  elsewhere.
- An admin chooses `report` first, looks at the conformity report, then switches to `enforce`.
- Importing this package also switches on the four-eyes defaults from Part 2/5, including for
  `config.import` itself. Further imports then need an approval.
