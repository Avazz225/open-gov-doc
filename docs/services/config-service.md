# config-service

**Verantwortung:** Konfigurationsimport/-export (Konzept 7.3, P12-S3): vollständige
Systemkonfiguration (Objekttypen inkl. Formular-Layouts, Workflows, Rollen-/Rechte-Templates,
Vier-Augen-Einstellungen je Aktionstyp, Sensor-Konfiguration) als ein JSON-Dokument exportierbar
und in ein anderes (oder dasselbe, z. B. Staging→Produktion) System re-importierbar —
Versionierung des Konfigurationsschemas selbst, damit Export aus einer älteren Version in eine
neuere importiert werden kann.

**Konzept-Referenz:** 7.3
**Eigenes Postgres-Schema:** keines — reiner Orchestrator, jede Kategorie wird direkt beim
jeweiligen Owner-Service gelesen/geschrieben (`object-type-service`, `workflow-service`,
`permission-service`, `monitoring-service`), gleiches "stateless orchestrator"-Muster wie
`webdav-connector` (P12-S1).
**ADR:** [0035 — Scope des Exports, Upsert-Semantik, Gating-Wiederverwendung](../adr/0035-config-service-scope-and-upsert-semantics.md)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/config/export` | Exportiert ein `ConfigDocument` — optional `?categories=roles&categories=workflows` zur Einschränkung, sonst alle fünf Kategorien |
| `POST` | `/config/import` | Wendet ein `ConfigDocument` an (Upsert je Kategorie) — verlangt `X-DMS-Principal`-Header mit `admin.object_config`-Berechtigung, sonst `403`; unbekannte `schema_version` ohne Migrationspfad → `422` |
| `GET` | `/healthz` | Health-Check (ungegated) |

## Die fünf Kategorien

| Kategorie | Owner-Service | Natürlicher Schlüssel (Upsert) | Besonderheit |
|---|---|---|---|
| `object_types` | object-type-service | `name` | Layouts nur, wenn `is_custom=true` (2.2b) — berechnete Smart-Layout-Defaults werden nie exportiert |
| `workflows` | workflow-service | — (immer neue Version) | Nur die aktuellste Version je Prozessdefinitions-Familie; Ziel-`workflow-service` versioniert beim Import automatisch neu (ADR 0027) |
| `roles` | permission-service | `name` | Rollen-Templates (`Role`), **nicht** ressourcengebundene `role_assignment`-Zeilen |
| `approval_config` | permission-service | `action_type` | Vier-Augen-Konfiguration (4.3) — Ziel-Endpunkt ist bereits ein Upsert |
| `sensor_config` | monitoring-service | — (Singleton + Overrides) | Globaler Default + Sensor-Overrides (10.1, P11-S1) |

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

## Bewusste Grenzen

- **Keine Selektion einzelner Einträge innerhalb einer Kategorie** — nur grobkörnige Kategorie-
  Filterung. Bei vielen angesammelten Workflow-Familien (z. B. aus Testläufen) exportiert
  `categories=workflows` daher alle aktuellen Familien; eine Namens-Allowlist wäre ein
  naheliegendes künftiges Feature.
- **Keine Kompatibilitätsprüfung zwischen Quell- und Zielsystem** vor dem Import (anders als
  `migration-service`s Dry-Run, ADR 0034) — Werte werden unverändert angewendet, ein
  konstraint-verletzender Eintrag landet als Fehler in `CategoryResult.errors`.

## Konfiguration

| Variable | Default | Bedeutung |
|---|---|---|
| `DMS_OBJECT_TYPE_SERVICE_BASE_URL` | `http://localhost:8007` | object-type-service |
| `DMS_WORKFLOW_SERVICE_BASE_URL` | `http://localhost:8014` | workflow-service |
| `DMS_PERMISSION_SERVICE_BASE_URL` | `http://localhost:8004` | permission-service |
| `DMS_MONITORING_SERVICE_BASE_URL` | `http://localhost:8026` | monitoring-service |
| `CONFIG_SERVICE_PORT` | `8029` | Host-Port im Dev-Compose-Stack |

## Tests

Läuft wie `webdav-connector`/`migration-service` gegen den echten, laufenden Container (kein
In-Prozess-`TestClient`, kein Mocking der Nachbar-Services) — `tests/conftest.py`s
`authorized_principal`-Fixture weist einem Testprinzipal vorübergehend `domain-admin-config` zu
und entfernt die Zuweisung danach wieder. Deckt ab: Export mit/ohne Kategorie-Filter, unbekannte
Kategorie (`422`), Import ohne/mit falschem Principal (`403`), nicht unterstützte
`schema_version` (`422`), Rollen-Upsert (create→update per Name), Vier-Augen-Konfig-Upsert, sowie
einen vollständigen Export→Reimport-Roundtrip.
