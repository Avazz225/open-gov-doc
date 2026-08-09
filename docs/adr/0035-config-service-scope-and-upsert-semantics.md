# 0035 — Config Service: Scope des Exports, Upsert-Semantik, Gating-Wiederverwendung

**Status:** akzeptiert
**Kontext:** P12-S3 (Konzept 7.3, "Konfigurationsimport/-export"). 7.3 verlangt: "Vollständige
Systemkonfiguration (Objekttypen, Constraints, Workflows, Rollen-/Rechte-Templates, Vier-Augen-
Einstellungen je Aktionstyp, UI-Anpassungen) exportierbar als JSON-Dokument ... Re-Import in ein
anderes (oder dasselbe, z. B. Staging→Produktion) System möglich ... Versionierung des
Konfigurationsschemas selbst, damit Export aus einer älteren Version in eine neuere importiert
werden kann." `config-service` selbst hat **kein eigenes Postgres-Schema** — jede Kategorie wird
direkt beim jeweiligen Owner-Service gelesen/geschrieben (Domain-Owner-Prinzip), dasselbe
Grundmuster wie `webdav-connector` (P12-S1, "stateless orchestrator").

## Entscheidung

**Fünf reale Kategorien statt der wörtlichen 7.3-Liste**: `object_types`, `workflows`, `roles`,
`approval_config`, `sensor_config`. "UI-Anpassungen" (Branding/Theming) und eine AD-Gruppen-
Mapping-Regel existieren an keiner Stelle im Code — beide wurden nach exhaustivem Grep über den
gesamten Baum bewusst **nicht** als fiktive, leere Kategorien erfunden, sondern ehrlich
ausgelassen (gleiche Disziplin wie schon bei P12-S2s Dry-Run-Grenzen).

**`is_custom`-gefilterter Layout-Export**: `object-type-service`s `GET /object-types/{id}/
layouts/{purpose}` liefert entweder ein echtes gespeichertes Override (`is_custom=true`) oder ein
rein berechnetes "Smart Layout" (`is_custom=false`), wenn nie explizit gespeichert wurde (Konzept
2.2b). Nur `is_custom=true`-Layouts wandern in den Export — sonst würde ein Re-Import einen
automatisch abgeleiteten Default fälschlich als dauerhaftes Override auf dem Zielsystem
einfrieren, obwohl das Quellsystem selbst nie eines gesetzt hatte.

**Workflows: nur die aktuellste Version je Familie** (`workflow-service`s `GET
/process-definitions` liefert bereits genau das) — nicht die volle Versionshistorie. Ein Re-Import
legt beim Ziel-`workflow-service` ohnehin automatisch eine neue Version unter demselben Namen an
(bestehendes Versionierungsverhalten, ADR 0027), Upsert-Semantik ist hier also für "created" vs.
"updated" nicht anwendbar — jeder Workflow-Import zählt bewusst als `created`.

**Upsert-per-Name für Objekttypen und Rollen**: beide Namen sind in ihrem jeweiligen DB-Schema
eindeutig (bestehende Constraints, nicht neu eingeführt). Import matched per `name` gegen die
Zielinstallation — existiert der Name bereits, wird aktualisiert statt dupliziert (Kernanforderung
für den Staging→Produktion-Anwendungsfall, bei dem ein wiederholter Import keine Duplikate
anhäufen darf). `approval_config` und `sensor_config` sind bereits auf Owner-Service-Seite echte
Upsert-Endpunkte (`PUT /approval-config/{action_type}`, `PUT /sensor-config/global`), kein
zusätzlicher Vorher-Check nötig.

**Neuer `PUT /roles/{role_id}`-Endpunkt in `permission-service`**: existierte bislang nicht (nur
`POST`/`GET`) — eine echte, zuvor unbekannte Lücke, aufgedeckt beim Bauen der Upsert-Logik für
`roles`. `name` bleibt beim Update unveränderlich (natürlicher Schlüssel für den
Konfigurationsabgleich).

**Gating über die bestehende `admin.object_config`-Capability statt einer neuen Domain-Admin-
Rolle**: `POST /config/import` verlangt dieselbe Berechtigung wie `workflow-service`s
Prozessdefinition-Upload — ein voller Konfigurationsimport ist eine Erweiterung derselben "Objekt-
typ-/Workflow-Konfiguration"-Verantwortung, keine eigene neue Domäne. `config-service` bootstrapped
sich beim Start selbst **beide** dafür nötigen Rollen (`domain-admin-config` UND
`domain-admin-monitoring`, da Sensor-Konfigurations-Schreibzugriffe zusätzlich `admin.monitoring`
verlangen, P11-S1) — identisches idempotentes Selbstzuweisungs-Muster wie `migration-service`s
`_ensure_config_admin_permission()` (P12-S2).

**Leeres `MIGRATIONS`-Registry als Erweiterungspunkt statt fiktiver Migrationen**: es gab bislang
nur `SCHEMA_VERSION = "1.0"`. `migrations.py` definiert bereits `upgrade_to_current()` (Loop mit
Zyklenerkennung, `422` bei unbekannter/nicht erreichbarer Version) und das Registry-Dict, aber
**keine** erfundene Migrationslogik für eine Version, die es noch nicht gibt — entspricht der
projektweiten Disziplin, nicht für hypothetische künftige Anforderungen zu bauen.

**`payload: dict` statt direkter `ConfigDocument`-Validierung am Import-Endpunkt**: die
Schema-Migration muss auf dem rohen Dict ansetzen können, *bevor* Pydantic gegen die aktuelle
Version validiert — eine ältere Exportversion mit inzwischen entfernten/umbenannten Feldern würde
sonst schon an der Validierung scheitern, bevor `upgrade_to_current()` überhaupt zum Zug kommt.

## Begründung

- **Kein eigenes Postgres-Schema**: jede Kategorie hat bereits einen Owner-Service mit
  vollständiger CRUD-API — ein dupliziertes `config`-Schema wäre reine Kopie ohne Mehrwert und
  eine zweite Quelle der Wahrheit, die aus dem Tritt geraten könnte.
- **Ehrliches Weglassen statt Erfinden**: "UI-Anpassungen"/AD-Gruppen-Mapping als leere,
  nicht-funktionale Platzhalter-Kategorien einzuführen hätte den Eindruck einer bereits
  vorhandenen Fähigkeit erweckt, die es nicht gibt.
- **`is_custom`-Filterung**: ein Export/Import-Zyklus muss den beobachtbaren Zustand des Quell-
  systems treu abbilden — ein Smart-Layout-Default ist kein vom Admin getroffener
  Konfigurationsentscheid und gehört daher nicht in eine "Konfiguration".

## Konsequenzen

- **Bewusste Grenze: keine Selektion einzelner Workflow-/Objekttyp-Einträge innerhalb einer
  Kategorie** — nur grobkörnige Kategorie-Filterung (`?categories=roles&...`). Bei einer
  Entwicklungsdatenbank mit hunderten über viele Testläufe angesammelten Workflow-Familien (real
  beobachtet: 317 beim Verifikations-Export dieser Session) exportiert `categories=workflows`
  daher **alle** aktuellen Familien — für eine produktive Staging→Produktion-Migration wäre eine
  Namens-Allowlist ein naheliegendes künftiges Feature, hier bewusst nicht gebaut (kein Bedarf laut
  7.3-Text, der nur "vollständige Systemkonfiguration" fordert).
- **Bewusste Grenze: keine Konflikterkennung bei widersprüchlichen `allowed_parent_types`/
  Constraints zwischen Quell- und Zielsystem** — ein Import wendet Werte unverändert an, eine volle
  Kompatibilitätsprüfung wie bei `migration-service`s Dry-Run (ADR 0034) wäre ein eigenständiges
  Feature.
- **Präzedenzfall für künftige Kategorien**: jede neue exportierbare Konfigurationsart (z. B. ein
  künftiges Branding-Feature) folgt demselben Muster — Owner-Service-Client, `is_custom`-artige
  Filterung falls zutreffend, Upsert-per-natürlichem-Schlüssel, eigener Eintrag in `CATEGORIES`.
