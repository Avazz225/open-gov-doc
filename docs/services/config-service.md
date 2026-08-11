# config-service

**Verantwortung:** Konfigurationsimport/-export (Konzept 7.3, P12-S3): vollständige
Systemkonfiguration (Objekttypen inkl. Formular-Layouts, Workflows, seit P14-S4 zusätzlich
DMN-1.3-Entscheidungstabellen, seit P14-S5 zusätzlich Geschäftskalender, Rollen-/Rechte-Templates,
Vier-Augen-Einstellungen je Aktionstyp, Sensor-Konfiguration, seit P17-S1 zusätzlich
Keycloak-Realm-Rollen) als ein JSON-Dokument exportierbar und in ein anderes (oder dasselbe, z. B.
Staging→Produktion) System re-importierbar — Versionierung des Konfigurationsschemas selbst, damit
Export aus einer älteren Version in eine neuere importiert werden kann. **Seit P17-S1** trägt
dasselbe Dokument optional ein `manifest` (Name/Version/Kompatibilitätsspanne/Beschreibung/
Herkunft/Lizenz) und wird damit zu einem benannten **Konfigurationspaket** (14.1) — siehe
"Konfigurationspakete" unten.

**Konzept-Referenz:** 7.3, 7.5, 14.1, 14.2
**Eigenes Postgres-Schema:** keines — reiner Orchestrator, jede Kategorie wird direkt beim
jeweiligen Owner-Service gelesen/geschrieben (`object-type-service`, `workflow-service`,
`permission-service`, `monitoring-service`, seit P17-S1 zusätzlich `auth-service`), gleiches
"stateless orchestrator"-Muster wie `webdav-connector` (P12-S1). Seit **P17-S3** dennoch ein
reiner NATS-**Konsument** ohne eigenen Stream (`ensure_stream=False`) — siehe "Vier-Augen für
`config.import`" unten.
**ADR:** [0035 — Scope des Exports, Upsert-Semantik, Gating-Wiederverwendung](../adr/0035-config-service-scope-and-upsert-semantics.md),
[0040 — Delta-Vergleich: Feld-Ebene-Diff, kein automatischer Cross-Installation-Abruf](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md),
[0058 — Konfigurationspakete: Manifest + `realm_roles`, Gateway-Routen-Trennung](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md),
[0060 — eGov-Paket Teil 2: Vier-Augen-Lücken geschlossen](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/config/export` | Exportiert ein `ConfigDocument` — optional `?categories=roles&categories=workflows` zur Einschränkung, sonst alle neun Kategorien |
| `POST` | `/config/compare` | **Seit P14-S1**: Delta-/Vergleichsfunktion (7.5) — Body `{compare, base?, categories?, ignore_regex?}`; fehlt `base`, wird der eigene aktuelle Live-Export als Basisinstanz verwendet. Rein lesend/diagnostisch, ungegated wie `GET /config/export`. `422` bei unbekannter Kategorie oder ungültiger `ignore_regex` |
| `POST` | `/config/import` | Wendet ein `ConfigDocument` an (Upsert je Kategorie) — verlangt `X-DMS-Principal`-Header mit `admin.object_config`-Berechtigung, sonst `403`; unbekannte `schema_version` ohne Migrationspfad → `422`. **Seit P17-S1 KEIN öffentlicher Gateway-Pfad mehr** (siehe "Gateway-Routen-Trennung" unten). **Seit P17-S3** optional per Vier-Augen gegated (`config.import`, s. u.) — Antwort `ImportActionResult` (`status: "applied"\|"pending_approval"`, `result`, `approval_request_id`) statt des bisherigen flachen `ImportResult` |
| `POST` | `/config/fleet-import` | **Seit P17-S1** (vorher derselbe Pfad wie `/config/import`): identische Anwendungslogik, aber ausschließlich für `fleet-management-service` — verlangt `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` (3a/P13-S2, [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)), kein RBAC-Zweig. Bleibt der öffentliche Gateway-Pfad. **Bewusst weiterhin ungegated** (s. u.) |
| `GET` | `/healthz` | Health-Check (ungegated) |

## Vier-Augen für `config.import` (4.3/14.2, seit P17-S3)

`POST /config/import` fragt vor der Anwendung `ApprovalClient.requires_approval("config.import")`
beim `permission-service` ab (identisches Client-Muster wie `document-service`s Force-Unlock-Gate) —
per Default (`requires_approval=false`) bleibt das Verhalten unverändert: sofortige Anwendung,
`status="applied"`. Ist die Genehmigungspflicht aktiviert (z. B. über das eGov-Paket, siehe
`packages/egov/`), wird stattdessen ein `ApprovalRequest` angelegt (`{document, categories}` als
`payload`) und `status="pending_approval"` zurückgegeben — `result` bleibt `null`, bis eine zweite
Person über `POST /approval-requests/{id}/approve` bestätigt. Die tatsächliche Anwendung läuft dann
asynchron über einen neuen, reinen NATS-Konsumenten (`consumer.py`, `durable="config-service"`,
abonniert `permission.approval.approved` auf dem von `permission-service` besessenen `permission`-
Stream, `ensure_stream=False`), der bei `action_type="config.import"` dieselbe
`_apply_config_document()`-Anwendungslogik wie der sofortige Pfad aufruft. `POST /config/fleet-import`
bleibt bewusst ungegated — der automatisierte Fleet-Agent-Provisionierungspfad hat kein
Mensch-im-Loop, der einen später ausstehenden Freigabe-Request sinnvoll bestätigen könnte
([ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)).

Da `config-service` sonst keine eigene Event-Bus-Anbindung hat, verlangt der Konsument
`DMS_NATS_URL` (`infra/docker-compose.yml`: `nats://nats:4222`) — ohne diese Variable fällt
`BaseServiceSettings` auf `nats://localhost:4222` zurück, was im Container nicht erreichbar ist
(bei P17-S3 selbst gefundener Bug, siehe [ADR 0060](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)).

## Die neun Kategorien

| Kategorie | Owner-Service | Natürlicher Schlüssel (Upsert) | Besonderheit |
|---|---|---|---|
| `object_types` | object-type-service | `name` | Layouts nur, wenn `is_custom=true` (2.2b) — berechnete Smart-Layout-Defaults werden nie exportiert |
| `workflows` | workflow-service | — (immer neue Version) | Nur die aktuellste Version je Prozessdefinitions-Familie; Ziel-`workflow-service` versioniert beim Import automatisch neu (ADR 0027) |
| `dmn_definitions` | workflow-service | — (immer neue Version) | **Seit P14-S4**: DMN-1.3-Entscheidungstabellen (7.1) — gleiches Muster wie `workflows`, nur die aktuellste Version je Familie, Ziel versioniert beim Import automatisch neu. Beim Import bewusst **vor** `workflows` angewendet (`imports.py`), da ein `businessRuleTask`s `camunda:decisionRef` nur auflösbar ist, wenn die referenzierte DMN-Familie beim Ziel-`workflow-service` bereits existiert |
| `business_calendars` | workflow-service | `name` | **Seit P14-S5**: Geschäftskalender für die SLA-Fristberechnung (7.1, [ADR 0042](../adr/0042-business-calendar-script-engine-injection.md)) — anders als `workflows`/`dmn_definitions` ein gewöhnlicher Upsert wie `roles` (KEIN Versionierungsmuster, ein Kalender wird fortlaufend gepflegt statt versioniert) |
| `roles` | permission-service | `name` | Rollen-Templates (`Role`), **nicht** ressourcengebundene `role_assignment`-Zeilen |
| `approval_config` | permission-service | `action_type` | Vier-Augen-Konfiguration (4.3) — Ziel-Endpunkt ist bereits ein Upsert |
| `sensor_config` | monitoring-service | — (Singleton + Overrides) | Globaler Default + Sensor-Overrides (10.1, P11-S1) |
| `federation_config` | workflow-service | — (Singleton) | Versionskompatibilitätsspanne für föderierte Workflows (7.4, P13-S3) — `PUT` löst dort sofort eine Re-Registrierung beim Federation Hub aus, siehe `docs/services/workflow-service.md` "Federation" |
| `realm_roles` | auth-service | Name (reine `list[str]`, kein `list[dict]`) | **Seit P17-S1** (14.1): Keycloak-Realm-Rollen (z. B. `dms-poststelle`, 2.5) — anders als `roles` oben (permission-services DB-basierte `Role`s) ein komplett getrenntes System. Anwendung idempotent über `create_realm_role(..., skip_exists=True)`, identisches Primitiv wie `bootstrap._ensure_dms_admin_role` |

Bewusst **nicht** enthalten: "UI-Anpassungen" (Branding/Theming) und AD-Gruppen-Mapping-Regeln —
beide existieren an keiner Stelle im Code (siehe ADR 0035), wurden also nicht als leere,
fiktive Kategorien erfunden.

## Upsert-Semantik

Import matched Objekttypen, Rollen und (seit P14-S5) Geschäftskalender per `name` (in ihrem
jeweiligen DB-Schema eindeutig) — ein bereits vorhandener Name wird aktualisiert statt dupliziert.
`approval_config`/`sensor_config` nutzen die bereits vorhandenen Upsert-Endpunkte ihrer
Owner-Services direkt. Workflows/DMN-Definitionen zählen immer als `created` (jeder Import legt
eine neue Version an, siehe oben) — Geschäftskalender NICHT, sie folgen stattdessen demselben
Muster wie Rollen (`created` bei neuem Namen, `updated` bei bestehendem). Jeder Eintrag wird
einzeln in einem `try`/`except` verarbeitet — ein fehlerhafter Eintrag (z. B. ein Attribut, das
auf dem Zielsystem gegen eine Constraint verstößt) landet in `CategoryResult.errors`, bricht aber
nicht den gesamten Import ab.

## Konfigurationspakete (14.1, seit P17-S1)

Ein Konfigurationspaket ist technisch weiterhin exakt ein `ConfigDocument` — nur um ein optionales
`manifest`-Feld erweitert:

```json
{
  "schema_version": "1.0",
  "exported_at": "2026-08-11T00:00:00Z",
  "manifest": {
    "name": "eGov-Konfigurationspaket",
    "version": "1.0.0",
    "compatibility_range": ">=1.0,<2.0",
    "description": "Standardkonfiguration für die deutsche öffentliche Verwaltung",
    "origin": "dms-project",
    "license": "MIT"
  },
  "object_types": [...],
  "realm_roles": ["dms-poststelle"]
}
```

`manifest` ist rein beschreibend — `compatibility_range` wird **nicht** automatisch gegen die
laufende Systemversion geprüft (analog zu `federation_config`s Kompatibilitätsspanne, die ebenfalls
nur informativ ist). Die Anwendung läuft vollständig über den bereits bestehenden
`POST /config/import` (additiv/Upsert, wiederholt anwendbar — 14.1 wörtlich: "auch auf eine bereits
laufende, teilweise anders konfigurierte Installation anwendbar"). Ein `ConfigDocument` ohne
`manifest` bleibt ein gewöhnlicher 7.3-Export/Import wie vor P17-S1. Vorschau vor Anwendung nutzt
das bereits bestehende `POST /config/compare` (7.5, P14-S1) — kein neuer Endpunkt, `base`
weglassen zieht automatisch den eigenen Live-Export heran ("was würde sich ändern, wenn ich dieses
Paket importiere"). Erste konkrete Bedienoberfläche: die neue Admin-UI-Seite `/config-packages/`
(siehe `docs/services/admin-ui.md`) — vorher hatte `config-service` gar keine Frontend-Anbindung.
Details/Begründung siehe [ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Gateway-Routen-Trennung: `config/import` vs. `config/fleet-import` (seit P17-S1)

Bis P17-S1 teilten sich RBAC-Aufrufer (echte, eingeloggte `config-admin`-Nutzer) und der
Fleet-Agent-Schlüssel (3a/P13-S2) denselben Gateway-Pfad `config-service:config/import`, der seit
ADR 0037 als öffentlich (kein Keycloak-Token-Zwang) markiert war. **Realer, bei der ersten
Admin-UI-Anbindung gefundener Bug**: für öffentliche Pfade validiert der Gateway grundsätzlich
keinen Bearer-Token und setzt `X-DMS-Principal` nie (`gateway_service.main.proxy`) — der
RBAC-Zweig von `_require_import_permission` war dadurch für JEDEN Aufruf über den Gateway
faktisch unerreichbar, auch für echte Admins. Seit P17-S1: `POST /config/import` ist ein
regulärer, Keycloak-Token-pflichtiger Pfad (reines RBAC, kein Fleet-Bypass mehr); der Fleet-Agent
ruft stattdessen den neuen, weiterhin öffentlichen `POST /config/fleet-import` auf. Details siehe
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Gating (7.3-Import)

`POST /config/import` verlangt dieselbe `admin.object_config`-Capability wie `workflow-service`s
Prozessdefinition-Upload (keine neue, eigene Domain-Admin-Rolle) — ein voller Konfigurationsimport
ist eine Erweiterung derselben Verantwortung. Da Sensor-Konfigurations-Schreibzugriffe zusätzlich
`admin.monitoring` (P11-S1) und die `realm_roles`-Kategorie zusätzlich `admin.user_management`
(P17-S1, `auth-service`s neues `POST /realm-roles`) verlangen, bootstrapped sich `config-service`
beim Start selbst **drei** Rollen (`domain-admin-config`, `domain-admin-monitoring`,
`domain-admin-users`) — idempotente Selbstzuweisung, identisches Muster wie `migration-service`s
`_ensure_config_admin_permission()` (P12-S2).

## Schema-Versionierung

`SCHEMA_VERSION = "1.0"` (bislang die einzige Version). `migrations.py` definiert bereits den
Erweiterungspunkt: ein `MIGRATIONS: dict[str, Callable[[dict], dict]]`-Registry plus
`upgrade_to_current()`, das auf dem **rohen Dict** ansetzt, bevor Pydantic gegen die aktuelle
Version validiert (eine ältere Exportversion mit inzwischen umbenannten Feldern würde sonst schon
an der Validierung scheitern). Ein nicht erreichbarer `schema_version`-Wert (kein Migrationspfad,
oder ein Zyklus) wird mit `422` abgelehnt statt stillschweigend falsch interpretiert. Bewusst
**keine** erfundene Migration für eine Version, die es noch nicht gibt.

## Anbindung des P11-S0-Fundes (Sensor-Konfiguration)

`monitoring-service`s `SensorConfigEntry` wurde in P11-S1 bewusst **eigenständig** persistiert
("Bewusst keine Anbindung an 7.3 — Konfigurationsexport existiert erst P12-S3"). Diese Session
schließt diese Lücke: `sensor_config` ist jetzt eine reguläre Export-/Import-Kategorie wie jede
andere.

## Anbindung des P13-S3-Fundes (Versionskompatibilität)

Konzept 7.4 verlangt wörtlich, dass die Versionskompatibilitätsspanne föderierter Installationen
"Teil des ohnehin schon versionierten Konfigurationsschemas (7.3)" ist - vor P13-S3 lebte sie
ausschließlich in `workflow-service`s `Settings`, nur per Container-Neustart änderbar. Neue
Kategorie `federation_config` schließt diese Lücke nach demselben Muster wie `sensor_config`
(P11-S0-Fund) - `WorkflowServiceClient.get_federation_config()`/`put_federation_config()`.

## Delta-/Vergleichsfunktion (7.5, seit P14-S1)

`POST /config/compare` vergleicht zwei `ConfigDocument`-Exporte gegeneinander — Basisinstanz
(Referenz) gegen Vergleichsinstanz, Ergebnis ist ein gerichteter Delta-Bericht je Kategorie
(`CategoryDelta`: `only_in_base`, `only_in_compare`, `differing`, `identical`). Rein
lesend/diagnostisch, verändert nichts an einer der beiden Seiten (7.5). Details/Begründung siehe
[ADR 0040](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md).

- **Zuordnung je Kategorie**: Listen-Kategorien (`object_types`/`workflows`/`roles` per `name`,
  `approval_config` per `action_type`) werden über ihr Namensfeld einander zugeordnet;
  `sensor_config`/`federation_config` sind Singletons, kein Namensabgleich nötig.
- **Vergleich auf Feld-Ebene**, kein rekursiver Tiefendiff — `differing` listet je zugeordnetem
  Eintrag genau die Top-Level-Felder auf, die sich unterscheiden (`{"base": ..., "compare": ...}`),
  nicht eine zeilengenaue Aufschlüsselung verschachtelter Strukturen wie Objekttyp-Layouts.
- **Ignore-Regex** (`ignore_regex: {"<kategorie>": "<muster>"}`, `"*"` als globaler Default) wird
  vor der Zuordnung auf beide Namen angewendet (`re.sub`, Teilstring-Entfernung — 7.5s Beispiel:
  numerische Präfixe wie `100_`/`101__` werden entfernt, sodass `100_testobjekt_typ_alpha` und
  `101__testobjekt_typ_alpha` als dasselbe Objekt gelten). Betrifft **ausschließlich** die
  Zuordnung — der Attributvergleich der übrigen Felder läuft unabhängig davon vollständig weiter,
  der Anzeigename im Bericht bleibt immer der rohe Basisinstanz-Wert.
- **`base` optional**: fehlt es, baut `config-service` den eigenen aktuellen Live-Export als
  Basisinstanz (`export.build_export`, dieselbe Funktion wie `GET /config/export`) —
  Anwendungsfall "was würde sich ändern, wenn ich dieses Dokument importiere" (7.5).
- **Installationsspezifische Daten sind strukturell ausgeschlossen**: Lizenzstand/
  Registry-Erreichbarkeit sind gar nicht Teil von `ConfigDocument`, 7.5s Ausschlussregel ist damit
  automatisch erfüllt.
- **CLI**: `dms config export [--category X]... [--file out.json]` und
  `dms config compare <compare.json> [--base base.json] [--category X]... [--ignore-regex '{"*": "..."}']`
  — erfüllen 7.5s "über das CLI-Tool (6.2) in strukturierter, skriptfähiger Ausgabe"
  (`-o json` liefert den vollständigen `CompareResult`, die Default-Tabelle eine
  Kategorien-Zusammenfassung mit Anzahlen).

## Bewusste Grenzen

- **Keine Selektion einzelner Einträge innerhalb einer Kategorie** — nur grobkörnige Kategorie-
  Filterung. Bei vielen angesammelten Workflow-Familien (z. B. aus Testläufen) exportiert
  `categories=workflows` daher alle aktuellen Familien; eine Namens-Allowlist wäre ein
  naheliegendes künftiges Feature.
- **Keine Kompatibilitätsprüfung zwischen Quell- und Zielsystem** vor dem Import (anders als
  `migration-service`s Dry-Run, ADR 0034) — Werte werden unverändert angewendet, ein
  konstraint-verletzender Eintrag landet als Fehler in `CategoryResult.errors`.
- **Kein automatisierter Cross-Installation-Abruf für `POST /config/compare`** (P14-S1, siehe
  ADR 0040) — beide Exporte müssen der aufrufenden Seite bereits vorliegen (je über den eigenen,
  regulär authentisierten `GET /config/export`-Zugriff auf die jeweilige Installation erzeugt,
  z. B. via `dms config export`), auch wenn beide Installationen an einem gemeinsamen Federation
  Hub teilnehmen. Bewusst kleiner geschnitten als 7.5s optional erwähnte Hub-Automatisierung, um
  weder `GET /config/export` öffentlich zu gaten noch `config-service` zu einem eigenen
  Föderationsteilnehmer zu machen.
- **Kein Tiefendiff verschachtelter Strukturen** — eine Änderung tief innerhalb eines
  Objekttyp-Layouts wird als "das gesamte `layouts`-Feld weicht ab" gemeldet, nicht zeilengenau.
- **Keine Admin-UI-Visualisierung des Vergleichs** in dieser Session — nur CLI und rohe API,
  siehe ADR 0040.

## Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `DMS_OBJECT_TYPE_SERVICE_BASE_URL` | `http://localhost:8007` | object-type-service |
| `DMS_WORKFLOW_SERVICE_BASE_URL` | `http://localhost:8014` | workflow-service |
| `DMS_PERMISSION_SERVICE_BASE_URL` | `http://localhost:8004` | permission-service |
| `DMS_MONITORING_SERVICE_BASE_URL` | `http://localhost:8026` | monitoring-service |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | auth-service (`realm_roles`-Kategorie, seit P17-S1) |
| `CONFIG_SERVICE_PORT` | `8029` | Host-Port im Dev-Compose-Stack |

## Tests

`test_compare.py` — reine Unit-Tests der Vergleichslogik (seit P14-S1), ohne laufenden Container:
`normalize()`/`resolve_pattern()`, `diff_list_category()`/`diff_singleton_category()` je für
nur-in-Basis/nur-in-Vergleich/identisch/abweichend, das Ignore-Regex-Beispiel aus 7.5 wörtlich
nachgebaut (numerische Präfixe, inhaltlicher Vergleich bleibt trotzdem vollständig), sowie
`compare_documents()` über alle neun Kategorien hinweg. Seit **P17-S1**: `diff_string_list_category()`
(neuer dritter Diff-Modus für die reine `list[str]`-Kategorie `realm_roles`) einzeln sowie als Teil
von `compare_documents()`.

`test_api.py` — läuft wie `webdav-connector`/`migration-service` gegen den echten, laufenden
Container (kein In-Prozess-`TestClient`, kein Mocking der Nachbar-Services) — **daher NICHT** in
`scripts/run-tests.sh`s `CONSUMER_SERVICES` (der Container muss während des Testlaufs erreichbar
bleiben, siehe [ADR 0060](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)) —
`tests/conftest.py`s `authorized_principal`-Fixture weist einem Testprinzipal vorübergehend
`domain-admin-config` zu und entfernt die Zuweisung danach wieder. Deckt ab: Export mit/ohne
Kategorie-Filter, unbekannte Kategorie (`422`), Import ohne/mit falschem Principal (`403`), nicht
unterstützte `schema_version` (`422`), Rollen-Upsert (create→update per Name),
Vier-Augen-Konfig-Upsert, einen vollständigen Export→Reimport-Roundtrip, sowie den
`federation_config`-Import (wirkt tatsächlich auf den laufenden `workflow-service`-Container). Seit
**P14-S1**: `POST /config/compare` gegen sich selbst (keine Abweichungen), ohne `base` (zieht den
eigenen Live-Export heran), mit echten inhaltlichen Abweichungen, mit Ignore-Regex-Zuordnung. Seit
**P17-S3**: `status="applied"` per Default, `config.import`-Vier-Augen-Gate liefert
`pending_approval` und importiert (noch) nichts (`test_import_with_approval_required_defers_execution`).
`test_consumer.py` (neu) — reiner Unit-Test von `consumer.make_handler` mit einem Fake-`apply_import`-
Callback statt echter DB/Downstream-Aufrufe: genehmigter `config.import` ruft den Callback korrekt
auf, fremde Aktionstypen werden ignoriert, fehlendes `document` im Payload wird geloggt statt zu
crashen, ein fehlschlagender Callback propagiert nicht (keine unbestätigte NATS-Nachricht)
(mit/ohne Regex derselbe Fall verglichen), ungültige Regex → `422`, unbekannte Kategorie → `422`.
Seit **P14-S4**: `dmn_definitions` in der Standard-Kategorienliste des Exports,
`dmn_definitions`-Anlegen erzeugt beim Wiederholen unter demselben Namen automatisch eine neue
Version (`created: 1` bei jedem Aufruf, gleiches Muster wie `workflows`), echt gegen den laufenden
`workflow-service`-Container verifiziert. Seit **P14-S5**: `business_calendars` in der
Standard-Kategorienliste des Exports, ein Import unter neuem Namen zählt als `created`, ein
wiederholter Import mit geändertem `non_working_dates` unter demselben Namen als `updated`
(Upsert-Semantik, anders als `dmn_definitions`), echt gegen den laufenden `workflow-service`-
Container verifiziert. Seit **P17-S1**: der Fleet-Agent-Key-Bypass wanderte von `/config/import`
zu eigenen Tests für `/config/fleet-import` (Erfolg mit korrektem Schlüssel, `403` bei falschem/
fehlendem Schlüssel), ein neuer Test bestätigt explizit, dass ein Fleet-Agent-Schlüssel auf
`/config/import` NICHT mehr durchgeht (RBAC-only), sowie ein `realm_roles`-Import-/Export-Roundtrip
(prüft echt gegen den laufenden `auth-service`-Container, dass die Realm-Rolle in Keycloak
existiert).

**`tools/cli`**: `test_config_commands.py` deckt `dms config export` (JSON-Ausgabe, Datei-Export,
`--category`-Query-Parameter) und `dms config compare` (liest Datei(en), sendet korrekten
Request-Body inkl. optionalem `base`/`ignore_regex`, Tabellen- vs. JSON-Ausgabe) ab — gemocktes
Gateway (`httpx.MockTransport`), kein laufender Stack nötig.
