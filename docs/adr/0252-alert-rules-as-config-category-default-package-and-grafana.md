# 0252 — Alert rules as a config category, a default package, and alarm levels in Grafana

**Status:** accepted
**Context:** P88-S7 (user request 2026-11-21, after the Phase 88 close). Builds on alerting
([ADR 0245](0245-sensor-alerting-thresholds-downtime-and-notification.md)) and on configuration
packages ([ADR 0058](0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)).

After P88-S6 every alert rule had to be created by hand on the sensors page. The user asked for:
- a default package that equips the shipped sensors with standard alarms, as JSON;
- the same alarms in Grafana, with the question whether they can be inherited from OG Doc or must be
  created again in Grafana.

## Decision 1 — Two list categories in config-service, not a new package format

A configuration package is an ordinary configuration document with a `manifest` (ADR 0058). So the
alarms travel as two new categories, with export, import, compare and four-eyes import for free:

**`alert_rules`:**
- Upsert by `name`. Only the definition is exported; the evaluated state is runtime data of the
  installation.
- **Unchanged → `skipped`, not saved again.** monitoring-service restarts a rule's streaks and rate on
  every save (ADR 0245). Re-applying a package must not reset alarms that are building up.
- **Duplicate names.** monitoring-service doesn't make names unique. The first match is updated. A
  unique constraint would break installations that already have duplicates, and the admin page shows
  them.
- **Errors.** monitoring-service's `422` detail is passed into the import result, not just the status
  line.

**`alert_downtime_windows`:**
- Upsert by `name`. Rules are named (`rule_names`), not referenced by id, because ids are local to an
  installation.
- Applied after `alert_rules`, so a package can ship rules and windows together.
- A rule name that doesn't exist is an error for that window. Dropping it silently would narrow the
  window to fewer rules than the package says.

**Why two list categories and not one bundle:** the plan wrote "one category with windows included". Two
list categories give the compare function (7.5) a per-rule and per-window diff instead of one diff over
the whole bundle. A package simply contains both.

## Decision 2 — `packages/monitoring-defaults`, imported deliberately

**Contents:** 19 rules on the shipped, default-on sensors plus a **disabled** example downtime window.
The rules cover storage, replication, service replicas, heartbeats, licence, virus findings, OCR,
notifications, archival transfers, trash, search indexing, rejected logins and node/process resources.

**Thresholds:**
- They are starting points: absolute values where a value is clearly wrong (a permanently failed
  replication), conservative limits otherwise.
- Repeat counts damp sensors that spike briefly (CPU, replication backlog).

**Not auto-seeded on start**, like the eGov package:
- An installation that already has its own rules would get duplicates or surprises.
- Alarm mails are an operational commitment someone should switch on knowingly.
- The import is idempotent: re-applying after an update adds new rules and leaves unchanged ones alone.

**Checked by a pure test** (`config-service/tests/test_packages.py`): the package parses, every rule would
pass monitoring-service's validation, and names are unique.

## Decision 3 — Grafana shows OG Doc's levels; it doesn't evaluate them

Three ways were weighed.

**(a) Grafana alerting:** provision the rules as Grafana alert rules.
- A second engine with its own thresholds semantics, its own contact points and its own mails.
- It drifts from the admin page as soon as someone edits a rule in one place.

**(b) Prometheus rule file:** load `GET /alert-rules/prometheus` into Prometheus and show `ALERTS{}`.
- A rule-file reload path and, for notification, Alertmanager (rejected for the default in ADR 0245).
- `ALERTS` only knows firing/pending, not warning vs. error with OG Doc's repeat counts.

**(c) Export the evaluated level as a metric:** monitoring-service appends
`monitoring_alert_level{rule,sensor}` to `GET /metrics`.
- Values: -1 unknown, 0 ok, 1 warning, 2 error.
- Every rule edited in OG Doc appears in Grafana on the next scrape, with nothing to create there.

**Chosen: (c).** So the answer to the user's question: the alarms are **inherited**, not created again in
Grafana. (b) stays an opt-in for installations that want Prometheus to alert.

**Details of (c):**
- **Read from the database on every scrape**, not cached. Only the replica holding the advisory lock
  evaluates (ADR 0245 Decision 4), so another replica's memory would lag.
- **`unknown` is -1, not a missing series.** A rule whose sensor went silent shows as such instead of as
  a gap.
- **Disabled rules are left out.**
- **Always on, outside the sensor catalog,** like `monitoring_scrape_failures_total`. Switching it via
  the catalog would be monitoring-service asking itself whether its own sensor is on.

**The dashboard** (`dms-sensor-overview.json`) gets an "Alarme" row at the top:
- a state timeline per rule (`max by (rule)`, so replicas of monitoring-service don't duplicate rows);
- three counters: rules in error, in warning, and without a value.

## Consequences

- config-service has 16 categories; `CATEGORIES`, export, import (order: rules before windows), compare
  and client methods for monitoring-service. No new permission: `config-service` already holds
  `admin.monitoring` for `sensor_config`.
- **Live:**
  - Importing the package created 19 rules and one window; a second import skipped all 19.
  - Grafana showed the timeline after the next provisioning reload.
  - The unknown rules have a clear reason: no licence installed, no replica expectations set, no
    rejected logins yet, a rate before its second evaluation.
- **Not in scope:**
  - Per-rule notification recipients (still `operations_admin_email`, ADR 0245).
  - Grafana annotations for level changes. The state timeline already shows the changes; the events
    are in the audit trail.
