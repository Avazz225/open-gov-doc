# config-service

**Verantwortung:** Konfigurationsimport/-export (Konzept 7.3, P12-S3): vollständige
Systemkonfiguration (Objekttypen inkl. Formular-Layouts, Workflows, Rollen-/Rechte-Templates,
Vier-Augen-Einstellungen je Aktionstyp, Sensor-Konfiguration) als ein JSON-Dokument exportierbar
und in ein anderes (oder dasselbe, z. B. Staging→Produktion) System re-importierbar —
Versionierung des Konfigurationsschemas selbst, damit Export aus einer älteren Version in eine
neuere importiert werden kann.

**Konzept-Referenz:** 7.3, 7.5
**Eigenes Postgres-Schema:** keines — reiner Orchestrator, jede Kategorie wird direkt beim
jeweiligen Owner-Service gelesen/geschrieben (`object-type-service`, `workflow-service`,
`permission-service`, `monitoring-service`), gleiches "stateless orchestrator"-Muster wie
`webdav-connector` (P12-S1).
**ADR:** [0035 — Scope des Exports, Upsert-Semantik, Gating-Wiederverwendung](../adr/0035-config-service-scope-and-upsert-semantics.md),
[0040 — Delta-Vergleich: Feld-Ebene-Diff, kein automatischer Cross-Installation-Abruf](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/config/export` | Exportiert ein `ConfigDocument` — optional `?categories=roles&categories=workflows` zur Einschränkung, sonst alle sechs Kategorien |
| `POST` | `/config/compare` | **Seit P14-S1**: Delta-/Vergleichsfunktion (7.5) — Body `{compare, base?, categories?, ignore_regex?}`; fehlt `base`, wird der eigene aktuelle Live-Export als Basisinstanz verwendet. Rein lesend/diagnostisch, ungegated wie `GET /config/export`. `422` bei unbekannter Kategorie oder ungültiger `ignore_regex` |
| `POST` | `/config/import` | Wendet ein `ConfigDocument` an (Upsert je Kategorie) — verlangt `X-DMS-Principal`-Header mit `admin.object_config`-Berechtigung, oder seit P13-S2 einen gültigen `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` (fleet-management-service, siehe [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)), sonst `403`; unbekannte `schema_version` ohne Migrationspfad → `422` |
| `GET` | `/healthz` | Health-Check (ungegated) |

## Die sechs Kategorien

| Kategorie | Owner-Service | Natürlicher Schlüssel (Upsert) | Besonderheit |
|---|---|---|---|
| `object_types` | object-type-service | `name` | Layouts nur, wenn `is_custom=true` (2.2b) — berechnete Smart-Layout-Defaults werden nie exportiert |
| `workflows` | workflow-service | — (immer neue Version) | Nur die aktuellste Version je Prozessdefinitions-Familie; Ziel-`workflow-service` versioniert beim Import automatisch neu (ADR 0027) |
| `roles` | permission-service | `name` | Rollen-Templates (`Role`), **nicht** ressourcengebundene `role_assignment`-Zeilen |
| `approval_config` | permission-service | `action_type` | Vier-Augen-Konfiguration (4.3) — Ziel-Endpunkt ist bereits ein Upsert |
| `sensor_config` | monitoring-service | — (Singleton + Overrides) | Globaler Default + Sensor-Overrides (10.1, P11-S1) |
| `federation_config` | workflow-service | — (Singleton) | Versionskompatibilitätsspanne für föderierte Workflows (7.4, P13-S3) — `PUT` löst dort sofort eine Re-Registrierung beim Federation Hub aus, siehe `docs/services/workflow-service.md` "Federation" |

Bewusst **nicht** enthalten: "UI-Anpassungen" (Branding/Theming) und AD-Gruppen-Mapping-Regeln —
beide existieren an keiner Stelle im Code (siehe ADR 0035), wurden also nicht als leere,
fiktive Kategorien erfunden.

## Upsert-Semantik

Import matched Objekttypen und Rollen per `name` (in ihrem jeweiligen DB-Schema eindeutig) — ein
bereits vorhandener Name wird aktualisiert statt dupliziert. `approval_config`/`sensor_config`
nutzen die bereits vorhandenen Upsert-Endpunkte ihrer Owner-Services direkt. Workflows zählen
immer als `created` (jeder Import legt eine neue Version an, siehe oben). Jeder Eintrag wird
einzeln in einem `try`/`except` verarbeitet — ein fehlerhafter Eintrag (z. B. ein Attribut, das
auf dem Zielsystem gegen eine Constraint verstößt) landet in `CategoryResult.errors`, bricht aber
nicht den gesamten Import ab.

## Gating (7.3-Import)

`POST /config/import` verlangt dieselbe `admin.object_config`-Capability wie `workflow-service`s
Prozessdefinition-Upload (keine neue, eigene Domain-Admin-Rolle) — ein voller Konfigurationsimport
ist eine Erweiterung derselben Verantwortung. Da Sensor-Konfigurations-Schreibzugriffe zusätzlich
`admin.monitoring` verlangen (P11-S1), bootstrapped sich `config-service` beim Start selbst
**beide** Rollen (`domain-admin-config` und `domain-admin-monitoring`) — idempotente
Selbstzuweisung, identisches Muster wie `migration-service`s `_ensure_config_admin_permission()`
(P12-S2).

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
| `CONFIG_SERVICE_PORT` | `8029` | Host-Port im Dev-Compose-Stack |

## Tests

`test_compare.py` — reine Unit-Tests der Vergleichslogik (seit P14-S1), ohne laufenden Container:
`normalize()`/`resolve_pattern()`, `diff_list_category()`/`diff_singleton_category()` je für
nur-in-Basis/nur-in-Vergleich/identisch/abweichend, das Ignore-Regex-Beispiel aus 7.5 wörtlich
nachgebaut (numerische Präfixe, inhaltlicher Vergleich bleibt trotzdem vollständig), sowie
`compare_documents()` über alle sechs Kategorien hinweg.

`test_api.py` — läuft wie `webdav-connector`/`migration-service` gegen den echten, laufenden
Container (kein In-Prozess-`TestClient`, kein Mocking der Nachbar-Services) —
`tests/conftest.py`s `authorized_principal`-Fixture weist einem Testprinzipal vorübergehend
`domain-admin-config` zu und entfernt die Zuweisung danach wieder. Deckt ab: Export mit/ohne
Kategorie-Filter, unbekannte Kategorie (`422`), Import ohne/mit falschem Principal (`403`), nicht
unterstützte `schema_version` (`422`), Rollen-Upsert (create→update per Name),
Vier-Augen-Konfig-Upsert, einen vollständigen Export→Reimport-Roundtrip, sowie (P13-S3) den
Fleet-Agent-Key-Bypass und den `federation_config`-Import (wirkt tatsächlich auf den laufenden
`workflow-service`-Container). Seit **P14-S1**: `POST /config/compare` gegen sich selbst (keine
Abweichungen), ohne `base` (zieht den eigenen Live-Export heran), mit echten inhaltlichen
Abweichungen, mit Ignore-Regex-Zuordnung (mit/ohne Regex derselbe Fall verglichen), ungültige
Regex → `422`, unbekannte Kategorie → `422`.

**`tools/cli`**: `test_config_commands.py` deckt `dms config export` (JSON-Ausgabe, Datei-Export,
`--category`-Query-Parameter) und `dms config compare` (liest Datei(en), sendet korrekten
Request-Body inkl. optionalem `base`/`ignore_regex`, Tabellen- vs. JSON-Ausgabe) ab — gemocktes
Gateway (`httpx.MockTransport`), kein laufender Stack nötig.
