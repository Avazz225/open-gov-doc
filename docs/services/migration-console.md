# migration-console

**Verantwortung:** Eigenständige Frontend-Anwendung für Transfer-Vorgänge gegen `migration-service` (7.2/8, wörtlich: "eine Migrations-Konsole für Transfer-Vorgänge"), P14-S2. Zwei Bereiche: Installations-Paarung verwalten und Transfers starten/beobachten.
**Konzept-Referenz:** 8, 7.2
**Kein eigenes Postgres-Schema** — reine clientseitig gerenderte SPA (statischer Export, gleiches Muster wie `apps/user-ui`, siehe [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), kein eigener Backend-Prozess.
**ADR:** [0041 — Scope + neue Cross-Instanz-Aufgabenliste, keine neue Autorisierungsschicht](../adr/0041-reviewer-ui-migration-console-scope-and-cross-instance-tasks.md)

## Ort im Repo

`apps/migration-console/` — bewusst **nicht** unter `services/` (Node/React-Toolchain statt Python-Service-Template, ADR 0006) und bewusst **nicht** Teil der Admin-UI (wörtliche Konzept-Vorgabe: "eigenständige ... Migrations-Konsole").

## Seiten

| Route | Zweck |
|---|---|
| `/login/` | Anmeldung (identisch zu user-ui/admin-ui/process-designer/reviewer-ui) |
| `/` | `TransferConsole` — Transfer-Übersicht/-Start, nur erreichbar mit gültiger Session |
| `/paired-installations/` | `PairedInstallationList` — Installations-Paarung |

`RequireAuth` rendert eine gemeinsame `Shell` (Kopfzeile mit Tab-Navigation, Theme-Umschalter, Logout) sowie zuvor ein `MaintenanceBanner` (Not-Shutdown, 4.8).

## Gepaarte Installationen (7.2, `components/PairedInstallationList.tsx`)

Direkte Installations-Paarung statt Hub-Vermittlung ([ADR 0034](../adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)) — anlegen (`POST /paired-installations`, leerer `api_key` lässt `migration-service` einen neuen generieren), auflisten, entfernen. Der generierte API-Key wird **nur unmittelbar nach dem Anlegen einmalig angezeigt** (nie wieder über `GET` abrufbar, identisches Prinzip wie `federation-hub-service`) — die Konsole zeigt ihn direkt im Formularbereich als Erfolgsmeldung an, nicht in der Tabelle.

## Transfers (7.2, `components/TransferConsole.tsx`)

Startformular (Quellordner-ID, Ziel-Installation aus der Paarungsliste, Dry-Run-Schalter, optionale Löschfrist in Tagen) ruft `POST /transfers`. Zwei mögliche Ausgänge:

- **`status: "started"`** — Transfer läuft direkt los, taucht sofort in der Liste auf.
- **`status: "pending_approval"`** — Vier-Augen (4.3) ist für `migration.transfer.start` aktiv konfiguriert (`permission-service`s `approval-config`); die Konsole zeigt einen Hinweis mit der erzeugten `approval_request_id` — die eigentliche Freigabe/Ablehnung läuft über die generische Freigabe-Inbox in `reviewer-ui`, nicht über diese Konsole selbst (keine doppelte Freigabe-UI).

Die Transferliste zeigt Status-Badge (grün für `released`/`deleted`/`dry_run_completed`, rot für `failed`, sonst neutral "in Bearbeitung"), Dokument-Fortschritt (`documents_copied`/`documents_total`/`documents_verified`), und eine aufklappbare Detailzeile mit Fehlermeldung (falls `failed`) sowie dem vollständigen Phasen-Zeitverlauf (`locked_at`/`copied_at`/`verified_at`/`released_at`/`deletion_scheduled_at`/`deleted_at`). **Leichtgewichtiges Polling alle 5 Sekunden** (`setInterval`, gleiches Muster wie `MaintenanceBanner`s 30s-Poll) — ein Transfer läuft als asynchrone `workflow-service`-Instanz im Hintergrund weiter, ohne erneutes Laden bliebe die Konsole auf dem Stand des letzten Aufrufs stehen.

**Bewusst nicht in der Konsole abgebildet**: die `.../steps/{lock|copy|verify|release|delete-source|dry-run-check}`-Endpunkte (interne Ziele automatischer `connector_call`-Service-Tasks, 7.1/P12-S2) und die gesamte `/inbound/*`-API (wird von der gepaarten Gegenseite aufgerufen, nie vom lokalen Bedienpersonal) — beides reine Workflow-/Protokoll-Mechanik ohne Bedienoberfläche.

## Autorisierung

**Keine capability-gegateten Aktionen in dieser App** — `migration-service` gated seine schreibenden Endpunkte ausschließlich über die eigene Lizenzprüfung (`license_gate`, Konzept 9.3: Demo-Modus sperrt `POST /transfers`/`POST /paired-installations`, nicht das Lesen), keine domänengetrennte Admin-Rolle. `RequireAuth` prüft nur, ob überhaupt eine gültige Sitzung vorliegt. Eine unlizenzierte oder im Demo-Modus befindliche Installation zeigt die entsprechende `403`-Fehlermeldung direkt im jeweiligen Formular an (kein eigener Lizenzstatus-Banner wie in der Admin-UI, aus Scope-Gründen dieser Session nicht ergänzt).

## Anbindung an das Backend

Ausschließlich über das API-Gateway (3.5):

| Aktion | Gateway-Aufruf |
|---|---|
| Anmelden | `POST /api/auth-service/login` |
| Identität nach Login | `GET /api/auth-service/me` |
| Gepaarte Installationen listen/anlegen/entfernen | `GET/POST/DELETE /api/migration-service/paired-installations[/{id}]` |
| Transfers listen/anlegen/Detail | `GET/POST /api/migration-service/transfers`, `GET /api/migration-service/transfers/{id}` |
| Theme-Präferenz lesen/schreiben | `GET/PUT /api/auth-service/me/preferences` |
| Not-Shutdown / Wartungsmodus-Status | `GET /api/permission-service/maintenance-mode` |

## Theming/i18n/Auth-Zustand

Identische Provider-Kopie aus user-ui/admin-ui/process-designer/reviewer-ui (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), eigenes `src/i18n/de.json`, globaler `dms.tokens`-Storage-Key (Single-Installation).

## Build & Auslieferung

Zweistufiges Docker-Image (`apps/migration-console/Dockerfile`, `node:22-alpine` Build-Stage → `nginx:alpine` Laufzeit), `NEXT_PUBLIC_GATEWAY_BASE_URL` als Build-Arg, überschreibbar über `MIGRATION_CONSOLE_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: Port `${MIGRATION_CONSOLE_PORT:-3004}:80`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript-Prüfung, ESLint, produktionsfähiger statischer Export.
- `npm test` (Vitest + Testing Library, **14 Tests**): `AuthProvider` (4 Tests, identisch zum Muster der übrigen Apps), `PairedInstallationList` (leere Liste, Auflistung, Anlegen inkl. einmaliger API-Key-Anzeige, Löschen nach Bestätigung, 4 Tests), `TransferConsole` (leere Liste, Auflistung mit aufgelöstem Ziel-Installationsnamen + Fortschritt, Detail-Zeitverlauf aufklappen, Transfer starten inkl. Neuladen, Vier-Augen-Hinweis bei `pending_approval`, Polling-Intervall feuert erneutes Laden, 6 Tests).
- Live gegen den gebauten Container in einem echten (headless) Browser verifiziert (Login, Transfer-Liste inkl. Detail-Zeitverlauf aufklappen, Installations-Paarung inkl. vollständigem Anlegen→Anzeigen-des-Einmal-Schlüssels→Löschen-Zyklus, Design-Umschalter auf Dunkel — jeweils ohne Konsolenfehler; die Listen zeigten dabei reale, aus früheren Testläufen (u. a. dem Selbst-Loopback-Test von P12-S2) liegen gebliebene Installationen/Transfers).

## Offene Punkte

- **Kein Server-Push** — reines Polling alle 5s statt WebSocket/SSE (siehe ADR 0041 "Konsequenzen").
- **Kein eigener Lizenzstatus-Banner** — anders als die Admin-UI zeigt diese Konsole den `migration-service`-Lizenzstatus nicht proaktiv an, nur reaktiv als Fehlermeldung bei einem gesperrten Schreibversuch.
- **Freigabe von `pending_approval`-Transfers läuft ausschließlich über `reviewer-ui`** — bewusst keine eigene, zweite Freigabe-UI in dieser Konsole (siehe "Transfers" oben).
- Übernimmt dieselben bereits dokumentierten Grenzen von `migration-service` selbst (siehe `docs/services/migration-service.md` "Bewusste Grenzen"): kein Ziel-Ordner-Auswahldialog (Zielordner landet immer an der Wurzel der Zielinstallation), keine historischen Zeitstempel bei kopierten Dokumentversionen.
