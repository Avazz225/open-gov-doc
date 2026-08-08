# plugin-orchestration-service

**Verantwortung:** Plugin Orchestration Service Grundgerüst (3.8) — verwaltet Manifeste "dazustellbarer" Elemente (Connectoren, Rendering-Backends, Regel-Plugins, ...), trifft und auditiert Cold-Start-Platzierungsentscheidungen, sampelt eine eigene, bewusst minimale Ressourcen-Stichprobe. Beantwortet die Frage "wo sollte etwas laufen" — im Unterschied zu `registry-service`, das nur "wer ist gerade erreichbar und lizenziert" beantwortet (Konzept 3.8 "Abgrenzung zur Registry").

**Konzept-Referenz:** 3.8
**Eigenes Postgres-Schema:** `orchestration` (Tabellen `plugin_manifest`, `plugin_resource_report`, `cluster_node`, `placement_decision`).

## Grenzen dieser Ausbaustufe (P10-S1)

Bewusste Scope-Entscheidungen aus Rückfragen bei Sessionstart, siehe `PROGRESS.md` "Orchestrierung & Rolling Updates":

- **Entscheidungs-/Empfehlungs-Engine, kein Container-Lifecycle-Manager.** Der Service berechnet und auditiert Platzierungsentscheidungen, startet aber selbst keine Container — kein Docker-Socket-Zugriff, kein neues Sicherheitsrisiko. Tatsächliches Starten/Stoppen bleibt extern (Mensch/Deploy-Skript), analog zum P8-S3-Präzedenzfall (Funktionen ohne echtes Backend werden dokumentiert statt vorgetäuscht).
- **Genau ein bekannter Knoten** (`cluster_node`, `node_id="self"`) — der Host, auf dem dieser Service selbst läuft, gesampelt per `psutil`. In der real existierenden Docker-Compose-Umgebung gibt es ohnehin nur diesen einen Host. Die "Wahl zwischen Knoten" (FFD-Bin-Packing über mehrere Knoten) ist daher noch trivial und **nicht** Gegenstand dieser Session.
- **Zeitprofil-Gruppierung, Plattform-Scheduler-Erkennung und der Drain-Mechanismus folgen erst in P10-S2/S3** — `load_profile` wird im Manifest bereits entgegengenommen/gespeichert, aber noch nicht ausgewertet.
- **Keine Docker-API für Ressourcenmessung** — Plugin-Instanzen melden ihre eigene, selbst gemessene Ressourcennutzung (z. B. via `psutil.Process()`) aktiv über `POST /plugins/{plugin_type}/resource-usage`, analog zum Registry-Heartbeat-Prinzip. Kein reales Plugin ruft das heute auf (existiert noch keins) — von Tests synthetisch abgedeckt, gleiches Muster wie `registry-service` vor P9-S2.
- **10.1-Sensor-Infrastruktur existiert erst Phase 11** (P10-S0-Befund) — die eigene Stichprobe hier ist eine bewusst minimale Übergangslösung, keine Vorwegnahme der vollwertigen Monitoring-Schicht.

## Architekturentscheidungen

- **Manifest-Format 1:1 aus dem Konzepttext**: `plugin_type` (Primärschlüssel, Upsert statt Versionsnebeneinander), `version`, `scaling_type` (`"stateless_horizontal"`/`"singleton"`), `resource_cpu_cores`/`resource_ram_mb` (optional), `load_profile` (optional, freier String), `dependencies` (Liste anderer `service_type`-Werte).
- **Cold-Start-Ressourcenschätzung mit drei Quellen** (`source`-Feld in `PlacementDecisionOut`): `"manifest"` (statische Werte im Manifest deklariert), `"observed_median"` (Median frischer `PluginResourceReport`-Werte desselben `plugin_type`, Konzept 3.8 wörtlich), `"default_fallback"` (dokumentierter Minimal-Default `0.5` Cores/`256` MB, falls weder Manifest noch Beobachtung vorliegt — eigener Wert, um diesen "kalten" Sonderfall vom echten Median-Fall zu unterscheiden). Reports älter als `resource_report_stale_after_seconds` (Default 60s) zählen nicht mit.
- **Singleton-Konflikterkennung**: `scaling_type="singleton"` + ein frischer Resource-Report einer anderen Instanz desselben Typs → `POST /placements` liefert `409` statt einer zweiten Platzierung.
- **Kapazitätsprüfung gegen den einzigen bekannten Knoten**: verfügbare Kerne = `cpu_cores * (1 - cpu_usage_percent/100)`; reicht die Schätzung nicht, wird die Entscheidung trotzdem persistiert (`placement_allowed=false`, `node_id=null`, `reason` gesetzt) statt verworfen — Audit-Pflicht aus 3.8 gilt auch für abgelehnte Anfragen.
- **Jede Platzierungsentscheidung wird auditiert** (3.8: "auch der Plugin Orchestration Service selbst ist ein Service, dessen Entscheidungen auditiert werden") — lokal in `placement_decision` (Read-Modell für `GET /placements`) UND als `orchestration.placement.decided`-Event, das `audit-service` konsumiert (`"orchestration.>"`).
- **Abhängigkeits-Check informativ, nicht blockierend** — die im Manifest gelisteten `dependencies` werden gegen `registry-service`s `GET /instances/{type}` geprüft (TTL-Cache, fail-open) und als `dependency_status` zurückgegeben, verhindern aber keine Platzierung (Konzept fordert das nicht).
- **Neue domänengetrennte Admin-Rolle `domain-admin-orchestration` (`admin.orchestration`)** — entsteht wie `admin.license` (P9-S1) erst mit dem tatsächlichen Feature, Konzept 4.6 nennt seine Domänen-Liste nur beispielhaft. Gate auf `POST /plugins/{type}` und `POST /placements`; `GET`-Endpunkte und das Resource-Usage-Self-Reporting bleiben ungegatet (service-zu-service, kein Principal).
- **Atomarer Upsert (`ON CONFLICT DO UPDATE`) statt Get-dann-Erzeuge für `cluster_node`** — bewusste Abweichung vom sonst üblichen Muster (z. B. `registry_service.repository.register`), weil hier tatsächlich zwei nebenläufige Schreiber dieselbe Zeile treffen können (Hintergrund-Sampler-Loop + z. B. ein Test, der den Knoten seedet) — ein Get-dann-Insert wäre eine echte Race Condition, kein nur theoretisches Risiko (in der Testentwicklung tatsächlich als `UniqueViolationError` aufgetreten).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/plugins/{plugin_type}` | Manifest registrieren/aktualisieren (Upsert). Verlangt `admin.orchestration` oder aktivierten Superuser. |
| `GET` | `/plugins` | Alle Manifeste. Ungegatet. |
| `GET` | `/plugins/{plugin_type}` | Ein Manifest, `404` falls unbekannt. Ungegatet. |
| `POST` | `/plugins/{plugin_type}/resource-usage` | Ressourcen-Selbstmeldung einer laufenden Instanz (`instance_id`/`cpu_cores`/`ram_mb`). Ungegatet, service-zu-service. |
| `GET` | `/nodes` | Gesampelte Knoten (aktuell genau einer). Ungegatet. |
| `POST` | `/placements` | Cold-Start-Platzierungsentscheidung anfordern. `404` bei unbekanntem Manifest, `409` bei Singleton-Konflikt. Verlangt `admin.orchestration` oder aktivierten Superuser. |
| `GET` | `/placements` | Platzierungshistorie (Audit-Read-Modell), optional `?plugin_type=`. Ungegatet. |

## Datenmodell

- `plugin_manifest` — `plugin_type` (PK), `version`, `scaling_type`, `resource_cpu_cores`/`resource_ram_mb` (nullable), `load_profile` (nullable), `dependencies` (JSON-Liste), `registered_at`/`updated_at`.
- `plugin_resource_report` — `instance_id` (PK), `plugin_type`, `cpu_cores`, `ram_mb`, `reported_at`.
- `cluster_node` — `node_id` (PK, in dieser Umgebung immer `"self"`), `cpu_cores`, `total_ram_mb`, `cpu_usage_percent`, `available_ram_mb`, `sampled_at`.
- `placement_decision` — `id`, `plugin_type`, `node_id` (nullable), `estimated_cpu_cores`/`estimated_ram_mb`, `source`, `placement_allowed`, `reason` (nullable), `dependency_status` (JSON), `decided_at`.

## Events

Publiziert (Stream `orchestration`): `orchestration.placement.decided` (`plugin_type`/`node_id`/`placement_allowed`/`source`).
Konsumiert: keine (kein eigener NATS-Consumer — nur Producer, wie `license-service`/`query-service`).

## Selbst-Registrierung

Wie jeder andere Service über `dms-registry-client` (3.2a).

## Tests

`services/plugin-orchestration-service/tests/` — 24 Tests: `test_placement.py` (Manifest-Quelle/Median-Fallback/Default-Fallback, Staleness, Singleton-Konflikt, Kapazitätsprüfung, Abhängigkeits-Status), `test_sampler.py` (`psutil`-Werte, Upsert-Idempotenz), `test_api.py` (Gate, Manifest-CRUD, Resource-Usage, Placement inkl. `409`, `GET /nodes`/`GET /placements`).

## Offene Punkte

- Keine echte Container-Automatisierung (siehe "Grenzen dieser Ausbaustufe") — bleibt bewusst offen, bis eine spätere Session das explizit anders entscheidet.
- FFD-Bin-Packing über mehrere Knoten, Zeitprofil-Gruppierung, Plattform-Scheduler-Erkennung, Drain-Mechanismus: P10-S2/S3.
- `load_profile` wird gespeichert, aber noch nicht ausgewertet (folgt mit der Zeitprofil-bewussten Platzierung in P10-S2).
- Eigene Ressourcen-Stichprobe ist eine Übergangslösung bis zur vollwertigen 10.1-Sensor-Infrastruktur (Phase 11).
