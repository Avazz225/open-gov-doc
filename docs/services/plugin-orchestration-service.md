# plugin-orchestration-service

**Verantwortung:** Plugin Orchestration Service (3.8) — verwaltet Manifeste "dazustellbarer" Elemente (Connectoren, Rendering-Backends, Regel-Plugins, ...), trifft und auditiert Platzierungsentscheidungen (Plattform-Scheduler bevorzugt, First-Fit-Decreasing über mehrere Knoten als Fallback, zeitprofil-bewusste Knotenauswahl), sampelt eine eigene, bewusst minimale Ressourcen-Stichprobe. Beantwortet die Frage "wo sollte etwas laufen" — im Unterschied zu `registry-service`, das nur "wer ist gerade erreichbar und lizenziert" beantwortet (Konzept 3.8 "Abgrenzung zur Registry").

**Konzept-Referenz:** 3.8
**Eigenes Postgres-Schema:** `orchestration` (Tabellen `plugin_manifest`, `plugin_resource_report`, `cluster_node`, `placement_decision`).

## Grenzen dieser Ausbaustufe (P10-S1/S2)

Bewusste Scope-Entscheidungen aus Rückfragen bei Sessionstart, siehe `PROGRESS.md` "Orchestrierung & Rolling Updates":

- **Entscheidungs-/Empfehlungs-Engine, kein Container-Lifecycle-Manager.** Der Service berechnet und auditiert Platzierungsentscheidungen, startet aber selbst keine Container — kein Docker-Socket-Zugriff, kein neues Sicherheitsrisiko. Tatsächliches Starten/Stoppen bleibt extern (Mensch/Deploy-Skript), analog zum P8-S3-Präzedenzfall (Funktionen ohne echtes Backend werden dokumentiert statt vorgetäuscht). Aus demselben Grund löst der Orchestrator **keine** automatische Drain-Auslösung am `registry-service` aus (siehe `docs/services/registry-service.md` "Drain-Mechanismus") — nur der Zustand/die Durchsetzung wurde in P10-S2 gebaut, nicht eine automatische Kopplung.
- **Genau ein real gesampelter Knoten** (`cluster_node`, `node_id="self"`, per `psutil`) — in der real existierenden Docker-Compose-Umgebung gibt es ohnehin nur diesen einen Host. Seit P10-S2 können weitere Knoten über `POST /nodes/{node_id}` deklariert werden (kein echter zweiter Host ruft das heute auf, gleiches "kein echter Aufrufer, aber dieselbe Selbstmelde-Logik"-Muster wie die Ressourcen-Selbstmeldung).
- **Plattform-Scheduler-Zweig: `NullSchedulerAdapter` bleibt der reale Zustand DIESES Projekts, aber seit P24-S4 existiert ein echter `KubernetesSchedulerAdapter`** (nur mock-getestet, siehe unten und [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md)) — `main.py` verdrahtet ihn nur, wenn `detect_platform_scheduler()` tatsächlich `"kubernetes"` liefert (`KUBERNETES_SERVICE_HOST` gesetzt). In dieser Docker-Compose-Entwicklungsumgebung ist diese Variable nie gesetzt, also bleibt `NullSchedulerAdapter` → FFD-Fallback unverändert der gelebte Zustand.
- **Keine Docker-API für Ressourcenmessung** — Plugin-Instanzen melden ihre eigene, selbst gemessene Ressourcennutzung (z. B. via `psutil.Process()`) aktiv über `POST /plugins/{plugin_type}/resource-usage`, analog zum Registry-Heartbeat-Prinzip. Kein reales Plugin ruft das heute auf (existiert noch keins) — von Tests synthetisch abgedeckt, gleiches Muster wie `registry-service` vor P9-S2.
- **Implizite Profilableitung aus historischer Beobachtung wird nicht gebaut** (Konzept 3.8 nennt sie explizit optional) — nur explizit im Manifest deklarierte `load_profile`-Werte fließen in die Knotenauswahl ein.
- **10.1-Sensor-Infrastruktur existiert erst Phase 11** (P10-S0-Befund) — die eigene Stichprobe hier ist eine bewusst minimale Übergangslösung, keine Vorwegnahme der vollwertigen Monitoring-Schicht.

## Architekturentscheidungen

- **Manifest-Format 1:1 aus dem Konzepttext**: `plugin_type` (Primärschlüssel, Upsert statt Versionsnebeneinander), `version`, `scaling_type` (`"stateless_horizontal"`/`"singleton"`), `resource_cpu_cores`/`resource_ram_mb` (optional), `load_profile` (optional, freier String), `dependencies` (Liste anderer `service_type`-Werte).
- **Cold-Start-Ressourcenschätzung mit drei Quellen** (`source`-Feld in `PlacementDecisionOut`): `"manifest"` (statische Werte im Manifest deklariert), `"observed_median"` (Median frischer `PluginResourceReport`-Werte desselben `plugin_type`, Konzept 3.8 wörtlich), `"default_fallback"` (dokumentierter Minimal-Default `0.5` Cores/`256` MB, falls weder Manifest noch Beobachtung vorliegt — eigener Wert, um diesen "kalten" Sonderfall vom echten Median-Fall zu unterscheiden). Reports älter als `resource_report_stale_after_seconds` (Default 60s) zählen nicht mit.
- **Singleton-Konflikterkennung**: `scaling_type="singleton"` + ein frischer Resource-Report einer anderen Instanz desselben Typs → `POST /placements` liefert `409` statt einer zweiten Platzierung.
- **First-Fit-Decreasing über alle bekannten Knoten** (P10-S2): Knoten werden aufsteigend nach `node_id` iteriert (Konzept 3.8 sortiert nur die zu platzierenden Instanzen, nicht die Knoten — eine feste Reihenfolge ist die einfachste korrekte Wahl), der erste mit ausreichend freier Kapazität (verfügbare Kerne = `cpu_cores * (1 - cpu_usage_percent/100)`) wird gewählt. Reicht keine Kapazität, wird die Entscheidung trotzdem persistiert (`placement_allowed=false`, `node_id=null`, `reason` gesetzt) statt verworfen — Audit-Pflicht aus 3.8 gilt auch für abgelehnte Anfragen.
- **Zeitprofil-bewusste Knotenauswahl vor dem reinen First-Fit** (P10-S2, Konzept 3.8: Zeitprofil "als zusätzliches Sortier-/Gruppierungskriterium **vor** der reinen Ressourcengrößen-Sortierung"): hat das zu platzierende Plugin ein `load_profile`, werden kapazitätsfähige Knoten nach Komplementarität ihrer aktuell dort "lebenden" Plugin-Typen sortiert — "lebend" = ein `plugin_type` mit frischem `PluginResourceReport` **und** dessen jüngste erlaubte `PlacementDecision.node_id` zeigt auf diesen Knoten. Score = Anzahl dort lebender Typen mit **unterschiedlichem** `load_profile` minus Anzahl mit **gleichem** `load_profile` (Beispiel: ein Nachtjob bevorzugt einen tagsüber ausgelasteten interaktiven Knoten). Ohne `load_profile` (heute der Regelfall) bleibt reines First-Fit.
- **Plattform-Scheduler bevorzugt** (P10-S2, Konzept 3.8): `decide_placement` fragt zuerst den injizierten `SchedulerAdapter`; liefert er einen Knoten, wird ihm vollständig vertraut (keine eigene Kapazitätsprüfung, `placement_method="platform_scheduler"`), sonst greift First-Fit+Zeitprofil (`placement_method="ffd"`). In dieser Docker-Compose-Entwicklungsumgebung immer `NullSchedulerAdapter` → immer `"ffd"` (siehe "Grenzen dieser Ausbaustufe").
- **`KubernetesSchedulerAdapter`** (P24-S4, [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md)) implementiert `SchedulerAdapter` echt gegen die Kubernetes-API. Aktivierung: `main.py` wählt ihn genau dann, wenn `detect_platform_scheduler()` `"kubernetes"` liefert (`KUBERNETES_SERVICE_HOST`-Env-Var gesetzt — dieses Signal existiert nur, wenn der Prozess selbst in einem Pod läuft), sonst bleibt es bei `NullSchedulerAdapter`. Authentifizierung ausschließlich über `kubernetes.config.load_incluster_config()` — bewusst **kein** Kubeconfig-Pfad für Out-of-Cluster-Nutzung (Begründung in ADR 0094). `try_place`:
  1. Listet Cluster-Knoten über `CoreV1Api.list_node()` (optional gescoped auf `settings.kubernetes_node_label_selector`, Default alle Knoten), blockierender Client-Aufruf über `asyncio.to_thread` ausgelagert.
  2. Überspringt Knoten mit `spec.unschedulable=true` oder ohne `Ready`-Condition.
  3. Vergleicht `cpu_cores`/`ram_mb` gegen `status.allocatable` je verbleibendem Knoten (Parsing von Kubernetes-Quantity-Strings wie `"500m"`/`"8Gi"` über `kubernetes.utils.parse_quantity`).
  4. **Kapazitäts-Vereinfachung (dokumentierte Limitierung, kein verschwiegener Kompromiss)**: geprüft wird ausschließlich `status.allocatable`, NICHT die Summe der `resources.requests` bereits laufender Pods auf dem Knoten — ein Knoten kann also fälschlich als passend gemeldet werden, obwohl er durch andere Pods bereits ausgelastet ist. Da `decide_placement()` einer Rückgabe von `try_place` ohne eigene Nachprüfung vertraut, ist das ein echtes, bewusst für diese erste Version eingegangenes Risiko (siehe ADR 0094 für die Abwägung).
  5. Tie-Break bei mehreren passenden Knoten: der mit der meisten frei allokierbaren RAM-Kapazität gewinnt ("most-available", spreizt Last stärker als reines First-Fit), bei Gleichstand `node_id` als deterministischer Zweit-Tie-Break.
  **Testlücke, ehrlich benannt (Honesty-Konvention dieses Projekts)**: es existiert in dieser Umgebung KEIN echtes Kubernetes-Cluster (einziges reales Deploy-Ziel bleibt Docker Compose; Phase 26 bringt nur `helm lint`/`helm template`-verifizierte Charts ohne echtes Cluster-Deployment) — `tests/test_platform_scheduler_kubernetes.py` mockt deshalb zwangsläufig den `kubernetes`-Client selbst (`CoreV1Api.list_node`, echte `kubernetes.client`-Modellobjekte wie `V1Node`/`V1NodeStatus` als Rückgabewerte) statt gegen einen echten API-Server zu laufen. Ein Lauf gegen ein reales Cluster hat nicht stattgefunden und war in dieser Umgebung nicht möglich.
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
| `GET` | `/nodes` | Gesampelte/deklarierte Knoten. Ungegatet. |
| `POST` | `/nodes/{node_id}` | Kapazitäts-Selbstmeldung eines (weiteren) Knotens (Upsert, P10-S2). Verlangt `admin.orchestration` oder aktivierten Superuser. |
| `POST` | `/placements` | Platzierungsentscheidung anfordern. `404` bei unbekanntem Manifest, `409` bei Singleton-Konflikt. Verlangt `admin.orchestration` oder aktivierten Superuser. |
| `GET` | `/placements` | Platzierungshistorie (Audit-Read-Modell), optional `?plugin_type=`. Ungegatet. |

## Datenmodell

- `plugin_manifest` — `plugin_type` (PK), `version`, `scaling_type`, `resource_cpu_cores`/`resource_ram_mb` (nullable), `load_profile` (nullable), `dependencies` (JSON-Liste), `registered_at`/`updated_at`.
- `plugin_resource_report` — `instance_id` (PK), `plugin_type`, `cpu_cores`, `ram_mb`, `reported_at`.
- `cluster_node` — `node_id` (PK, `"self"` = eigener gesampelter Host, weitere via `POST /nodes/{id}`), `cpu_cores`, `total_ram_mb`, `cpu_usage_percent`, `available_ram_mb`, `sampled_at`.
- `placement_decision` — `id`, `plugin_type`, `node_id` (nullable), `estimated_cpu_cores`/`estimated_ram_mb`, `source`, `placement_method` (`"platform_scheduler"`/`"ffd"`, P10-S2, additiv per `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` nachgerüstet), `placement_allowed`, `reason` (nullable), `dependency_status` (JSON), `decided_at`.

## Events

Publiziert (Stream `orchestration`): `orchestration.placement.decided` (`plugin_type`/`node_id`/`placement_allowed`/`source`/`placement_method`).
Konsumiert: keine (kein eigener NATS-Consumer — nur Producer, wie `license-service`/`query-service`).

## Selbst-Registrierung

Wie jeder andere Service über `dms-registry-client` (3.2a).

## Tests

`services/plugin-orchestration-service/tests/` — 43 Tests: `test_placement.py` (Manifest-Quelle/Median-Fallback/Default-Fallback, Staleness, Singleton-Konflikt, Mehrknoten-First-Fit, Zeitprofil-Ranking, Plattform-Scheduler-Delegation/-Fallback, Abhängigkeits-Status), `test_sampler.py` (`psutil`-Werte, Upsert-Idempotenz), `test_api.py` (Gate, Manifest-CRUD, Resource-Usage, Node-Upsert-Gate, Placement inkl. `409`, `GET /nodes`/`GET /placements`), `test_platform_scheduler_kubernetes.py` (P24-S4, `KubernetesSchedulerAdapter` gegen gemockten `kubernetes`-Client: passender Knoten, keine Kapazität → `None`, Mehrknoten-Tie-Break, Unschedulable-/Not-Ready-Filterung, Millicore-/Binär-Einheiten-Parsing, Label-Selector-Weiterreichung).

## Offene Punkte

- Keine echte Container-Automatisierung (siehe "Grenzen dieser Ausbaustufe") — bleibt bewusst offen, bis eine spätere Session das explizit anders entscheidet.
- Keine automatische Kopplung an `registry-service`s Drain-Mechanismus (Rebalancing löst heute keinen echten Drain-Aufruf aus) — bewusste Scope-Grenze, siehe "Grenzen dieser Ausbaustufe".
- `KubernetesSchedulerAdapter` (P24-S4) prüft nur `status.allocatable`, nicht die tatsächlich durch laufende Pods verbrauchte Kapazität (kein Pod-Usage-bewusstes Bin-Packing) — dokumentierte Vereinfachung, siehe [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md). Nur In-Cluster-Konfiguration unterstützt, kein Kubeconfig-Pfad für Out-of-Cluster-Nutzung. Nie gegen ein echtes Cluster getestet (keins in dieser Umgebung verfügbar) — nur gegen einen gemockten `kubernetes`-Client.
- Implizite Profilableitung aus historischer Beobachtung nicht gebaut (Konzept nennt sie explizit optional) — nur explizit deklarierte `load_profile`-Werte fließen ein.
- Eigene Ressourcen-Stichprobe ist eine Übergangslösung bis zur vollwertigen 10.1-Sensor-Infrastruktur (Phase 11).
- Rolling Updates (Wiederverwendung des Drain-Mechanismus für Update-Rollouts, Expand/Contract): P10-S3.
