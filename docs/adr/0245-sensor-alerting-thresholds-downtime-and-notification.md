# 0245 — Alerting on sensors: thresholds with repeat counts, downtime windows, notification

**Status:** accepted
**Context:** P88-S5 (Phase 88, user-requested). Builds on the sensor catalog
([ADR 0243](0243-sensor-group-and-cost-overrides-and-admin-page.md)), on the replica alarms of
[ADR 0241](0241-service-instance-replacement-and-expected-replicas.md) and on a comparison with an
external reference system.

DMS has had sensors since P11-S1, with a catalog of 49 since P88-S2b/c, but nothing evaluates them. Only
registry-service's replica alarms (ADR 0241) mail anyone. The reference system evaluates every sensor
against four thresholds:
- **Thresholds:** min error, min warning, max warning, max error.
- **Repeat counts:** a separate count for warning and for error, so a single outlier doesn't raise an
  alarm.
- **Status:** each resource has a status (ok, warning, error, unknown), and groups roll up to the worst.
- **Notification:** by mail per level change.
- **Downtime windows:** they suppress notification.

The user asked for this as its own plan item.

## Decision 1 — Evaluate in monitoring-service, export to Prometheus as an extra

Two ways were weighed.

**(a) Alert rules generated for Prometheus plus an Alertmanager:**
- Standard tooling, and history in Prometheus.
- But it needs Alertmanager as a new component in compose and Helm, plus a rule-file reload path and
  its own mail routing.
- It only works where Prometheus actually runs. The Helm chart doesn't require it.
- It doesn't connect to the admin UI, the audit trail or notification-service's templates and rate
  limits.

**(b) Evaluation in monitoring-service:**
- It already scrapes every instance on demand (`GET /metrics`, `scraper.merge_metric_families`).
- The evaluation reuses exactly that merge, so it sees the same values Prometheus would.
- State changes are events, so the audit trail sees them, and mail goes through notification-service
  like the replica alarms.

**Chosen: (b).** For installations that run Alertmanager anyway, `GET /alert-rules/prometheus`
renders the same thresholds as a Prometheus rule file. (b) stays the source of truth, and (a) is an
export, not a second engine.

## Decision 2 — A rule is a sensor, an aggregation and four thresholds

**Shape.** `alert_rule` holds:
- `name`, `sensor` (the dotted name from the catalog);
- an optional `label_filter` (for example `{"target": "local"}` or `{"service": "ocr-service"}`), which
  matches a subset of a sample's labels, including the merge's own `service`/`instance`;
- `aggregation`, `min_error`/`min_warning`/`max_warning`/`max_error` (each optional, at least one set, in
  that order), `warning_repeat`/`error_repeat` (≥ 1), `enabled` and a description.

**Aggregations:**
- Gauges: `max`, `min`, `sum`, `avg` over the matching samples.
- Counters and histograms: `rate_per_minute`, the increase of the summed `_total`/`_count` since the
  last evaluation, per minute.
  - A counter reset (a smaller total) counts the new total as the increase.
  - The last total is persisted, so a different replica taking over (Decision 4) continues the rate.
- Histogram quantiles are out of scope: they need bucket interpolation over a window. The
  `…duration_seconds` histograms are alertable through their rate.

**No samples → `unknown`.** This happens when the sensor is off (ADR 0243), the service is down or the
filter matches nothing.
- `unknown` is a level of its own. It doesn't notify and doesn't count as resolved.
- The admin UI names the reason when the sensor is off.

## Decision 3 — Levels with repeat counts; notification follows the level, not each change

Per evaluation, with the reference system's rule:
- **Error breach:** the value is below `min_error` or above `max_error`. **Warning breach:** an error
  breach, or below `min_warning`/above `max_warning`.
- **Streaks:** an error breach increments `error_streak`, otherwise it resets to 0. A warning breach
  increments `warning_streak`, otherwise it resets to 0.
- **Level:**
  - `error` once `error_streak ≥ error_repeat`;
  - otherwise `warning` once `warning_streak ≥ warning_repeat`;
  - otherwise `ok`.
  - So an error not yet repeated often enough shows as warning, and a value back in range is `ok` at
    once.

**`alert_state`** holds per rule: `level`, `value`, `since`, both streaks, `last_total`/`last_total_at`
(rate), `evaluated_at` and `notified_level`.
- Every level change publishes `monitoring.alert.state_changed`, for audit and history.
- **Notification is decoupled:** a mail is due when the level is `warning`/`error`/`ok` and differs from
  `notified_level`, and no downtime window covers the rule.
  - `monitoring.alert.raised` is published for warning/error, `monitoring.alert.resolved` for ok after
    a notified alarm.
  - `notified_level` is then set.
  - An initial `ok` is not mailed.
- **Consequences of the decoupling:**
  - A change inside a downtime window is mailed when the window ends, if it is still the level then.
  - An alarm that clears during the window isn't mailed at all.
  - A level that flaps faster than notification is collapsed to what is true at notification time.

## Decision 4 — One evaluator across replicas

Each tick (default 60 s, `alert_evaluation_interval_seconds`) runs inside a transaction that first takes
`pg_try_advisory_xact_lock`.
- Only the replica holding it evaluates; the others skip the tick.
- The streaks and the rate therefore advance once per tick, however many replicas run.
- Notifications aren't duplicated: the events are published after the commit.

## Decision 5 — Downtime windows suppress notification, not evaluation

**Shape.** `downtime_window` has:
- `name`, an optional absolute range (`starts_at`/`ends_at`);
- an optional weekly recurrence (`weekdays` 0–6 plus `daily_start`/`daily_end` as local time in the
  installation's time zone, `Europe/Berlin` by default; an end before the start spans midnight);
- `rule_ids` (null = every rule) and `enabled`.

**Effect.** A window active now suppresses notification for its rules. The levels keep being evaluated
and shown, so the admin page reflects reality during maintenance.

## Decision 6 — Who may do what

- **Writes** (rules, windows) need `admin.monitoring`, the role that already governs sensor activation.
  Every change is an event with the admin as actor.
- **Reads** (`GET /alert-rules`, `/downtime-windows`) are ungated, like `GET /sensors`.
- **Recipient:** notification-service mails `operations_admin_email`, the operations mailbox of the
  replica alarms. There are two template use cases.

## Consequences

**P88-S5 (this session):**
- monitoring-service: the tables `alert_rule`, `alert_state`, `downtime_window`; `alerting.py` (pure
  evaluation); the evaluator loop with the advisory lock; CRUD; the Prometheus export; events.
- notification-service: the consumers and templates.
- Tests for every rule above.

**P88-S6 (new):** the admin UI on the sensors page.
- Rules per sensor, with level badges in the catalog and the worst level per group.
- A rule editor and the downtime windows.

**Not in scope:**
- A Nagios/CheckMK endpoint (declined in P11-S2, still declined).
- Escalation chains and SMS (the reference has SMS; DMS has no SMS channel).
- Histogram quantiles.
- ~~Alert rules in the configuration export (7.3); a candidate once the rules prove portable.~~ Done in
  P88-S7 ([ADR 0252](0252-alert-rules-as-config-category-default-package-and-grafana.md)), together
  with a default package and the alarm levels in Grafana.

## Implementation status

- **P88-S5 (done):** the backend as described.
  - **Live:** a rule raised and resolved with mails to ops-admin.
  - **Export:** `promtool check rules` accepts the Prometheus file.
- **P88-S6 (done):**
  - the UI on the sensors page: "Aktive Alarme", the "Alarm" column with worst levels per sensor and
    group, the rule editor, and the windows;
  - a preview endpoint (`POST /alert-rules/preview`), so the editor shows the current value and the
    matching series before saving.
