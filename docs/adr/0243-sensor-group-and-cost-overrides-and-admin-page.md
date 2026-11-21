# 0243 — Sensor overrides per group and cost class, and the admin sensors page

**Status:** accepted
**Context:** Konzept 10.1, P88-S2 (Phase 88).

## Where it stood

Monitoring has had its sensor catalog and activation config since P11-S1: `GET /sensors`,
`GET /sensor-config`, and `PUT /sensor-config/global|{sensor}`, gated by `admin.monitoring` and audited.
It only resolved two levels: a sensor's own override, else the global default. There was no admin page.

Concept 10.1 asks for more:
- "individual sensors **or entire sensor groups**";
- marking expensive sensors so that "everything on" isn't a burden, for example by "deliberately switching
  off expensive tracing sensors during continuous operation".

With overrides per sensor only, switching off a group or a cost class means one override per sensor. A
sensor that appears later (a new service, a new version) isn't covered.

## Decision 1 — Four levels, one rule, resolved where the data is generated

Override keys live in the same `sensor_config` table:
- a sensor name;
- `group:<group>`;
- `cost:cheap` or `cost:expensive`.

**The most specific setting wins:** sensor, then group, then cost class, then the global default.

- **One implementation.** The rule is `dms_metrics_client.resolve_active`. It is used by:
  - `SensorConfigClient.is_active(name, group=, cost=)` in every service;
  - monitoring-service's catalog, which also reports where the value came from (`source`: `sensor`,
    `group`, `cost` or `global`).
- **Resolution in the service, not in monitoring-service.** `SensorRegistry` binds each `Guarded*`
  sensor to its own spec's group and cost. A sensor that is off by its group or cost class therefore
  generates nothing, the same as an individually disabled one (10.1: "no generation"). A plain
  `is_active(name)` callable (tests, simple callers) keeps working without group and cost.
- **Group before cost.** A group is a deliberate, topic-specific choice ("storage sensors on for a
  capacity problem"), so it overrides the blanket cost switch. Turning a single expensive sensor on
  despite `cost:expensive = off` takes its own override.
- **Validation.** Keys with an unknown prefix, an unknown cost class or an empty value are refused
  (`422`), because they would silently match nothing.
- **Config export unchanged.** `config-service` exports and imports `overrides` as a dict and passes the
  new keys through unchanged.
- **Rollout.** Services built against the old client ignore group and cost keys until they are rebuilt.
  They then fall back to their own override or the global default, the old behavior; nothing breaks.

## Decision 2 — The admin page shows the whole picture and is readable without the role

`/sensors/` in admin-ui ("Monitoring-Sensoren", under Verwaltung) has these sections:
- **Grundeinstellung:** "Alles überwachen" / "Nichts überwachen", plus how many sensors are active.
- **Nach Kosten:** Günstig and Teuer, each with Übernehmen/An/Aus and its active count.
- **Gruppen und Sensoren:**
  - a header row per group with its own switch;
  - per sensor: name, description, a cost badge, services (collapsed to a count above three), state and
    where it comes from, and its own switch;
  - a filter by name, description or service.
- **Einstellungen ohne laufenden Sensor:** overrides for sensors or groups no running service declares.
  They can be reset; otherwise they would linger invisibly until the sensor returns.

The catalog is readable for every admin. Changes need `admin.monitoring`; without it the page is
read-only and says so.

The tracing sampling ratio named in the plan doesn't exist yet. It joins the page with P91-S1.

## Consequences

- `dms-metrics-client`:
  - `resolve_active`, `override_key_for_group`/`_cost`;
  - `SensorConfigClient`/`SensorConfigProxy.is_active` take `group`/`cost`;
  - `SensorRegistry._check_for`.
- monitoring-service: `SensorOut.source`, a catalog sorted by group and name, and
  `validate_override_key`.
- admin-ui: `SensorSettings.tsx`, `/sensors/`, a nav entry, and the `sensors.*` i18n keys.
- Every service image needs a rebuild to honour group and cost keys. The dev stack was rebuilt in
  P88-S2.
