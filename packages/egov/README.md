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

## Aktenplan Object-Type Hierarchy (Part 1, P17-S2)

Contains the `object_types` category:

| Object Type | Type | Parent | Attributes | Reference-Number Format | Retention | VS Classification |
|---|---|---|---|---|---|---|
| `Abteilung` | Folder | `$ROOT` | — | — | — | — |
| `Aktenplan` | Folder | `Abteilung` | — | — | — | — |
| `Akte` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10 years (3653 days) | — |
| `Verschlusssache-Akte` | Document | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10 years (3653 days) | VS-NfD |

\* Required attribute. The reference-number generation itself (`{Laufende_Nummer}`, year-based
reset) is the already existing reference-number generator (P5e sessions) — `{Federführung}` is a
newly supported, **attribute-based** placeholder since P17-S2 (any placeholder that is not a
date/counter placeholder is interpreted as an attribute name), a direct implementation of the
concept example `{Abteilung}-{YYYY}-{Laufende_Nummer}` — here deliberately using the actually
more sensible `Federführung`, which is already a required attribute, instead of a redundant
second "Abteilung" attribute (the Akte is structurally already located under an
`Abteilung` folder instance anyway).

The retention period (10 years) and the choice to default `Verschlusssache-Akte` to `VS-NfD`
instead of a higher level are **changeable defaults, not a system requirement** — the concrete
legal deadline/classification remains the responsibility of the installation per federal
state/legal domain (concept text, verbatim).

## Mailroom, Process Templates, Four-Eyes, Business Calendar, Admin Roles (Part 2, P17-S3)

Extends the same `config.json` (14.1: additive/upsert, no new file) with the remaining five
components mentioned in §14.2 — `manifest.version` has been set to `1.0.0` since this session,
the first complete version of the package. Details/rationale: see
[ADR 0060](../../docs/adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md).

| Category | Content |
|---|---|
| `realm_roles` | `dms-poststelle` — Keycloak realm role for incoming/outgoing mail (2.5), already enforced by `mail-connector` (since P15-S3), packaged here for the first time. |
| `workflows` | Three BPMN process templates for the Umlaufmappe (circulation folder) pattern (2.3/7.1): `egov_freigabe` (approve/reject decision via `exclusiveGateway`), `egov_kenntnisnahme` and `egov_aufgabe` (each a linear `manualTask`). Source XML is additionally located under [`workflows/`](workflows/) for easier maintenance/diff readability. |
| `approval_config` | Four-eyes default (4.3) for the three sensitive action types named in 14.2 — `document.force_delete`, `folder.force_delete`, `document.force_unlock` (permanent deletion), `permission.role_assignment.create` (permission change, actually enforced since P17-S3), `config.import` (configuration import, actually enforced since P17-S3) — each with `requires_approval: true`. |
| `business_calendars` | `DE-Bund` (default, nine nationwide holidays) plus 16 state calendars `DE-BW` … `DE-TH` (each including federal holidays), real dates for 2026/2027. |
| `roles` | `Registratur/Aktenverwaltung` (`read`, `write`) and `Amtsleitung` (`read`, `write`, `scope_lock.bypass`) — extended, domain-separated admin roles on top of the technical `domain-admin-*` system roles (4.6). |

The four-eyes default requires that the approving person **not** be identical to the
initiating person (4.3) — after applying this package, permanent deletion, role assignment,
and configuration import therefore generally require a second person before they take
effect (`POST /approval-requests/{id}/approve`).
