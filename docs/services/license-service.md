# license-service

**Verantwortung:** Lizenzverwaltung/-prüfung (9.1/9.2/9.3) — verwaltet eine signierte Lizenzdatei (JWT/RS256, [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)), prüft laufend (nicht nur beim Start) die aktuelle Nutzung gegen vier Dimensionen und publiziert Statusänderungen als Events. `registry-service` konsumiert diese Events und vermittelt daraus einen Lizenzstatus je Komponente (P9-S2, siehe `docs/services/registry-service.md`), `document-service` fragt `GET /license/status` direkt ab, um Neuanlagen bei überschrittenem Dokumentenlimit zu blockieren.

**Konzept-Referenz:** 9.1, 9.2, 9.3
**Eigenes Postgres-Schema:** `license` (Tabelle `installed_license`, Singleton-Zeile — genuin eigener Zustand, keine Duplikation fremder Daten).

## Architekturentscheidungen

- **Signaturverfahren: JWT/RS256, statisch eingebetteter öffentlicher Schlüssel** ([ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)) — Wiederverwendung von `python-jose[cryptography]`, das bereits über `libs/dms-auth-client`s `TokenValidator` (Keycloak-JWT-Verifikation) in jedem Service-Container vorhanden ist. Kein JWKS-Fetch, keine Rotation in dieser Ausbaustufe.
- **Nur eine ungültige Signatur führt zum Ablehnen des Uploads (`400`)** — eine signaturgültige, aber bereits abgelaufene Lizenz wird trotzdem gespeichert und über `GET /license/status` als ungültig/abgelaufen angezeigt. Bildet die reale Situation ab ("das ist die aktuell installierte Lizenz, sie ist nur abgelaufen") statt eines Sonderfalls beim Hochladen.
- **Vier Konzept-9.1-Dimensionen als JWT-Claims**: `user_model` (`"concurrent"|"named"`), `max_users`, `storage_limit_gb`, `document_limit`, `licensed_components` — jede `null`-Wertig = "unlimited" (Konzept 9.1 wörtlich).
- **Nutzungsdaten-Quellen — direkte Service-zu-Service-Aufrufe, kein Umweg über reporting-service**: `storage-service`s `GET /storage/usage` (Summe `total_size_bytes`), `document-service`s neuer `GET /documents/count-active-total` (installationsweit, kein Ordnerfilter — anders als das bestehende, ordnergefilterte `POST /documents/count-active`, P7-S1b), `auth-service`s neue `GET /sessions/count`/`GET /users/count` (je nach `user_model`-Claim nur der jeweils relevante Aufruf). Alle drei Ziel-Services bleiben Quelle der Wahrheit für ihre eigenen Daten (Service-Isolation).
- **`auth-service`s `GET /users` ist für interne Aufrufe ungeeignet** — gegated über `Depends(get_current_user)` (echtes Keycloak-Bearer-Token), das kein Service besitzt. Die beiden neuen Endpunkte `GET /sessions/count`/`GET /users/count` sind deshalb bewusst ungegatet (interner Aufruf, gleiche Begründung wie z. B. `permission-service`s `/role-assignments`). `GET /sessions/count` nutzt `KeycloakAdmin.get_client_sessions_stats()` (fertige Admin-API-Methode, kein neues Session-Tracking).
- **Poll-Loop statt Push** (9.2: "prüft laufend, nicht nur beim Start") — gleiches Idiom wie `document-service`s `_retention_poll_loop`/`workflow-service`s SLA-Timer (ADR 0020), Intervall 3600s. Ein Fehler in einem Tick bricht die Schleife nicht ab.
- **Flankenerkennung statt Event-Spam** — `InstalledLicense.last_status_snapshot` (JSON) hält fest, welche Zustände (ungültig/bald-ablaufend/pro Dimension überschritten) beim letzten Tick bereits gemeldet wurden; Events nur bei tatsächlichem Zustandswechsel, nicht bei jedem Tick. Eine Neuinstallation setzt den Snapshot zurück.
- **Drei Events, 1:1 die in 9.2 genannten Statusänderungs-Arten**: `license.limit_exceeded` (`dimension`/`current`/`limit`), `license.expiring_soon` (`days_remaining`, Schwelle 30 Tage), `license.invalid` (`reason`). Zusätzlich `license.installed` beim Upload. `audit-service`s Subjects-Liste hat `"license.>"` bekommen.
- **`notification-service` konsumiert alle drei Flanken-Events** (Konzept 9.2 nennt ihn wörtlich als Konsumenten) — feste `settings.license_admin_email`-Adresse, kein Empfänger-Auflösungsmechanismus, 1:1 Kopie von `_handle_maintenance_mode_activated`. Da alle drei Subjects den neuen `"license"`-Stream teilen, brauchte jedes einen eigenen Durable-Namen (`notification-service-license-*`) — derselbe Durable-Name für mehrere Filter-Subjects auf demselben Stream schlägt mit "consumer is already bound to a subscription" fehl, gleiche Einschränkung wie zuvor schon bei `workflow.federation.inbound_received`.
- **`admin.license`-Gate aktiviert erstmals die seit Langem vorgeseedete Domain-Admin-Rolle `domain-admin-license`** — `POST /license` verlangt sie (oder den aktivierten Superuser), 1:1 Gate-Muster aus `query-service`. `GET /license/status` bleibt ungegatet (wird von `registry-service` in P9-S2 und später der Admin-UI ohne Principal-Header abgefragt).
- **Kein Lizenzausstellungswerkzeug in diesem Repo** (ADR 0032) — der private Schlüssel existiert ausschließlich außerhalb des Systems beim Lizenzgeber. Der Test-Fixture-Schlüssel (`tests/fixtures/dev_private_key.pem`) ist ausdrücklich ein Wegwerf-Entwicklungsschlüssel, kein Bestandteil eines Ausstellungswerkzeugs.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/license` `{license_token}` | Signierte Lizenzdatei installieren — `400` bei ungültiger Signatur, sonst `201` auch bei abgelaufener Lizenz. Verlangt `admin.license` oder aktivierten Superuser. |
| `GET` | `/license/status` | Aktueller Lizenzstatus + Nutzung je Dimension (`installed`/`valid`/`invalid_reason`/`issued_at`/`expires_at`/`days_remaining`/`user_model`/`users`/`storage_gb`/`documents`/`licensed_components`/`limits_exceeded`). Ungegatet. |

## Datenmodell

`license.installed_license` — Singleton (`id=1`): `raw_token`, `installed_at`, `installed_by`, `issued_at`, `expires_at`, `last_status_snapshot` (JSON, Flankenerkennung).

## Events

Publiziert (Stream `license`): `license.installed`, `license.limit_exceeded`, `license.expiring_soon`, `license.invalid`.
Konsumiert: keine (kein eigener NATS-Consumer — nur Producer, wie `query-service` vor P8-S2).

## Selbst-Registrierung

Wie jeder andere Service über `dms-registry-client` (3.2a) — unabhängig von der in P9-2 geplanten Lizenz-Vermittlungs-Funktion der Registry selbst.

## Tests

`services/license-service/tests/` — 25 Tests: `test_license_verifier.py` (Signaturprüfung, inkl. abgelaufenes-aber-signaturgültiges Token), `test_usage.py` (Dimension-Grenzwert-Logik inkl. "unlimited"), `test_poll_loop.py` (Flankenerkennung), `test_api.py` (Upload-Gate, Statusendpunkt inkl. "keine Lizenz installiert").

## Offene Punkte

- Keine Schlüsselrotation/kein JWKS (ADR 0032) — ein kompromittierter privater Schlüssel erfordert ein neues `license-service`-Release mit neuem öffentlichen Schlüssel.
- Installations-ID (9.2: "Jede Installation registriert sich ... mit einer eigenen Installations-ID") nicht durchgesetzt — kein Selbstidentitäts-Konzept existiert bislang irgendwo im System (P9-S0-Fund), Lizenzclaim bliebe rein informativ, falls künftig ergänzt.
- "Applikationskomponenten"-Dimension (`licensed_components`) wird seit P9-S2 durchgesetzt, aber nur für `workflow-service` — die einzige heute real existierende licensierbare Komponente (CMIS-Connector/Migration-Service kommen erst in Phase 12).
- Nutzungslimit-Blockade (9.3) bislang nur für die Dokumentenzahl umgesetzt (`document-service`s `POST /documents`) — Speicher-/Nutzerlimits verhindern aktuell keine Neuanlagen, nur die Statusanzeige/Events erfassen sie.
