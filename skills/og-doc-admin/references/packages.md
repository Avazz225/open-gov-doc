# Configuration packages

- **What a package is:** a JSON document (a configuration export plus a `manifest`) with one key per
  category. Shipped packages: `packages/egov/config.json` (eGov defaults) and
  `packages/monitoring-defaults/config.json` (standard alert rules).
- **Commands:**
  - `dms config export [--category …] [--file out.json]` reads the current state;
  - `dms config compare <file>` shows what an import would change against the live state;
  - `dms config import <file> [--category …]` applies it.
- **Roles needed:** `domain-admin-config-read` for export and compare, `domain-admin-config` for import.
- **Import is an upsert:** existing entries are matched by their natural key (mostly `name`) and updated,
  new ones created, unchanged ones skipped. Nothing is deleted. Workflows and DMN tables always become a
  new version.
- **Four-eyes:** if `config.import` requires approval, import returns `pending_approval` and changes
  nothing until a second person approves.
- **The categories,** applied in this order (later ones refer to earlier ones by name):

| Category | What | Key |
|---|---|---|
| `object_types` | document and folder types, attributes, retention defaults, filing rules | name |
| `workflows` | BPMN process definitions | new version |
| `dmn_definitions` | decision tables | new version |
| `business_calendars` | working days for SLA deadlines | name |
| `roles` | permission bundles | name |
| `approval_config` | four-eyes per action type | action type |
| `sensor_config` | which sensors are on | singleton |
| `federation_config` | federation version range | singleton |
| `realm_roles` | Keycloak realm roles | name |
| `ad_group_mappings` | AD group → role mappings | mapping |
| `branding_config` | product name, colour, logo | singleton |
| `deletion_reason_catalogs` | deletion reasons (additive merge) | singleton |
| `org_units` | groups marked as org units | name |
| `org_unit_role_links` | role for every member of an org unit | org unit |
| `alert_rules` | alert thresholds on sensors | name |
| `alert_downtime_windows` | alert suppression windows | name |
| `export_config` | PDF export and output stamp | singleton |
| `structure_config` | filing structure: root lists, enforcement | singleton |
| `folder_templates` | folder structure templates | name |

- **A fragment** (a small JSON with only the categories you change) is the usual way to make a change
  reviewable. Build it from `dms -o json config export --category <c>`, change the entry, keep the
  structure, then `scripts/preview-package.sh fragment.json` and, after a yes, `dms config import
  fragment.json`.
- **Errors per entry** are reported per category; the import continues with the other entries. The CLI
  exits with code 1 if any entry failed. Show the messages.
