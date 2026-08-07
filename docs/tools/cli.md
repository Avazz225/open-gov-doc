# tools/cli — DMS-CLI

**Verantwortung:** Vollständiges Kommandozeilenwerkzeug (6.2) — ein Client gegen das API-Gateway
(3.5), konzeptionell an `oc` (OpenShift) orientiert. Jeder Aufruf läuft über dieselbe
`/api/{service_type}/{path}`-Route mit Bearer-Token wie die Web-UIs (`apps/admin-ui`,
`apps/user-ui`), das CLI respektiert also dieselben Rechte/Sicherungsstufen (RBAC,
Bereichssperren, Vier-Augen, Not-Shutdown) ohne eigenen Umgehungspfad.

**Konzept-Referenz:** 6.2
**Kein Service** — kein eigenes Postgres-Schema, kein `main.py`/FastAPI, kein Eintrag in
`infra/docker-compose.yml` (siehe "Docker" unten). Liegt unter `tools/cli/` statt
`services/<name>/`, gleiche Begründung wie `apps/*` in `docs/service-template.md`: kein
Backend-Dienst, andere Tooling-Konventionen.

## Umfangsentscheidung (P8-S3)

Konzept 6.2 listet einen breiten Funktionsumfang (Konfigurationsim-/-export, Query & Trace
Konsole, Migrations-/Transfer-Vorgänge, Objekttypen/Constraints/Workflows,
Registry-/Plugin-Orchestrierungsstatus, Lizenzstatus, Nutzer-/Rollenverwaltung,
Backup/Restore, Delta-/Vergleichsläufe). Zum Zeitpunkt dieser Session (nach Phase 8) hat nur
ein Teil davon ein echtes Backend — der Rest gehört laut `IMPLEMENTATION_PLAN.md` zu späteren
Phasen. Statt Attrappen zu bauen oder den Umfang stillschweigend zu verkleinern, ist hier
ehrlich dokumentiert, was abgedeckt ist und was auf welche künftige Phase wartet:

| Konzept-Bullet | Stand | CLI-Abdeckung |
|---|---|---|
| Query & Trace Konsole (6.1) | ✅ `query-service` | `dms query ...` |
| Objekttypen/Constraints (2.2) | ✅ `object-type-service` | `dms object-type ...` |
| Workflows (7.1) | ✅ `workflow-service` | `dms workflow ...` |
| Nutzer-/Rollenverwaltung (4.4/4.1/4.6) | ✅ `auth-service`/`permission-service` | `dms user ...`, `dms role ...` |
| Registry-Status (3.2) | ✅ `registry-service` | `dms registry status` |
| Plugin-Orchestrierungsstatus (3.8) | ❌ existiert nicht (Phase 10) | — |
| Migrations-/Transfer-Vorgänge (7.2) | ❌ generischer 7.2-Dienst existiert nicht (Phase 12, P12-S2) | `dms archival ...` deckt die **nächstliegende reale Entsprechung** ab: `archival-service`s Aussonderungs-Transfers (5.6) — thematisch verwandt (Sperren→Kopieren/Paketieren→Verifizieren→Freigabe), aber fachlich nicht dasselbe wie 7.2 |
| Konfigurationsim-/-export (7.3) | ❌ existiert nicht (Phase 12, P12-S3) | — |
| Lizenzstatus (9.3) | ❌ existiert nicht (Phase 9) | — |
| Backup/Restore (10.4) | ❌ existiert nicht (Phase 11) | — |
| Delta-/Vergleichsläufe (7.5) | ❌ existiert nicht (Phase 14) | — |

Gleiches Muster wie bei P7-S3s Umgang mit dem damals ebenfalls fehlenden 7.2-Vorbild: Umfang
ehrlich auf echte Endpunkte begrenzt, Rest hier und in `PROGRESS.md` als offener Punkt benannt.

## Architekturentscheidungen

- **Python + Typer statt neuem Ökosystem** — passt zum bestehenden `uv`-Monorepo-Stack.
  `pyproject.toml` (Root) hat dafür `tools/*` zu `[tool.uv.workspace].members` bekommen
  (bisher nur `libs/*`/`services/*`).
- **Gateway-Client 1:1 wie `apps/*-ui/src/lib/api.ts`** — `{gateway_url}/api/{service_type}/{path}`,
  `Authorization: Bearer <token>`, keine direkten Backend-Adressen (`tools/cli/src/dms_cli/client.py`).
- **Credential-Speicherung in `~/.dms/credentials.json`** (chmod 600) statt eines
  Systemschlüsselbunds — funktioniert ohne Zusatzabhängigkeit auf jeder Plattform gleich.
  `dms login` (Password-Grant über `POST /api/auth-service/login`, wie die Web-UIs) schreibt
  Access-/Refresh-Token; `dms logout` löscht die Datei.
- **Transparenter 401-Refresh + Retry** (`GatewayClient._refresh_once`) — die Web-UIs rufen
  ihre eigene `refreshToken()`-Funktion aktuell nirgends auf (geprüft: 0 Call-Sites in
  `apps/admin-ui`/`apps/user-ui`), für einen kurzlebigen Browser-Tab bisher irrelevant. Für ein
  CLI, dessen Access-Token über viele kurze Prozessaufrufe hinweg abläuft, ist ein automatischer
  Refresh dagegen notwendig, sonst müsste vor praktisch jedem zweiten Aufruf neu `dms login`
  laufen.
- **`DMS_GATEWAY_URL`/`DMS_TOKEN`-Env-Vars überschreiben die Datei** — der CI/CD-Pfad: eine
  Pipeline injiziert einen anderweitig beschafften Token, ohne dass das CLI etwas in eine Datei
  schreibt. Siehe "Offene Punkte" zum Grund, warum es (noch) keinen `client_credentials`-Grant
  gibt.
- **Domänen-Gliederung in getrennte `commands/<domain>.py`-Module** (`query`, `object_type`,
  `user`, `role`, `registry`, `workflow`, `archival`, plus `auth`/`config`), jedes mit eigener
  Typer-Sub-App. Erfüllt Konzept 6.2s "modular aufgebaut... in fachliche Teilkommandos
  gegliedert" strukturell — eine buchstäbliche Aufteilung in separat ausrollbare Einzel-Binaries
  ist eine spätere Packaging-Entscheidung, kein Blocker dieser Session.
- **Ausgabeformat**: Default eine abhängigkeitsfreie Tabelle (`output.py`), `--output json`/`-o
  json` global für Skripte — erfüllt "scriptfähige Ausgabeformate wie JSON" direkt, ohne ein
  Rendering-Package (`rich` o. ä.) einzuführen.
- **Vier-Augen/Manipulation identisch zur Admin-UI** — `dms query manipulate dry-run`/`execute`
  + `dms query approvals list`/`approve` sprechen dieselben `query-service`/`permission-service`-
  Endpunkte wie `QueryConsoleView`s `ManipulationSection` (P8-S2b), inkl. derselben
  hartkodierten Liste der drei kuratierten Aktionstypen (dupliziert statt service-übergreifend
  importiert — Service-Isolation, siehe `CONTRIBUTING.md`). Konzept 6.1 verlangt wörtlich,
  dass "dieselbe Abfragesprache und dieselben Sicherungsstufen" in UI und CLI gelten.
- **Docker ohne Compose-Eintrag** — eigenes `Dockerfile` (uv-Image, `ENTRYPOINT ["uv", "run",
  "dms"]`) für den CI/CD-Anwendungsfall (`docker run dms-cli ...` ohne lokale Python-Umgebung),
  aber **kein** Eintrag in `infra/docker-compose.yml`: kein Health-Endpoint, kein lang laufender
  "up"-Zustand, das Compose-Neustartmuster passt nicht auf ein Einmal-Kommando.

## Kommandoübersicht

| Domäne | Befehle |
|---|---|
| Anmeldung | `dms login [--username] [--password] [--gateway-url]`, `dms logout`, `dms whoami` |
| Konfiguration | `dms config show`, `dms config set-gateway-url <url>` |
| Query & Trace (6.1) | `dms query events list [--actor] [--subject] [--event-type] [--since] [--until] [--limit]`, `dms query text "<...>"`, `dms query manipulation-mode status\|activate [--minutes]\|deactivate`, `dms query manipulate dry-run --action <type> --param k=v...`, `dms query manipulate execute --dry-run-token <token>`, `dms query approvals list\|approve <id> [--approved-by]` |
| Objekttypen (2.2) | `dms object-type list\|get <id>\|create -f <file>\|update <id> -f <file>\|delete <id>` |
| Nutzer (4.4) | `dms user list\|create --username --email --first-name --last-name [--password]\|delete <id>` |
| Rollen (4.1/4.6) | `dms role list`, `dms role assignment list [--principal] [--resource]\|create ...\|delete <id>` |
| Registry (3.2) | `dms registry status [service_type]` |
| Workflow (7.1) | `dms workflow definitions list\|get <id>`, `dms workflow instances list [--status]\|get <id>\|tasks <id>\|complete-task <instance> <task> --completed-by [-f data.json] [--signature-id]` |
| Aussonderung (5.6, siehe Tabelle oben) | `dms archival transfers list [--status]\|get <id>\|retrieve <id>`, `dms archival case-transfers list\|get <id>\|package <id> --out <path>` |

Jeder Befehl akzeptiert das globale `--output table\|json` (`-o`), muss **vor** dem
Unterbefehl stehen (`dms -o json query events list`).

## Offene Punkte

- **Kein `client_credentials`-Grant für echte Service-Accounts** — `auth-service`s Keycloak-
  Client hat `serviceAccountsEnabled: False` (`bootstrap.py`). Ein sauberer Service-Account pro
  CI/CD-Pipeline bräuchte eigene vertrauliche Keycloak-Clients (Client-Management-API) — ein
  eigenständiges, mehrsessiges Feature, keine Erweiterung dieser Session. Übergangsweise:
  `DMS_TOKEN`-Env-Var (s. o.).
- Die 7.2/7.3/9.3/10.4/7.5-Bullets aus Konzept 6.2 haben noch kein Backend — siehe Tabelle oben,
  jeweils mit Verweis auf die vorgesehene Phase. Kein CLI-Code für sie vorhanden.
- Kein Ablehnen-Befehl für Vier-Augen-Anfragen (`dms query approvals`) — gleiche bewusste
  Lücke wie in der Admin-UI (P8-S2b): Ablehnen ist bereits generisch über `permission-service`
  möglich, kein konsolenspezifischer Mehrwert in dieser Session.

## Tests

`tools/cli/tests/` — 70 Tests: `GatewayClient` (401-Refresh-Retry, Fehlerpfade), Credential-
Speicherung (Env-Override, Dateirechte), Ausgabeformatierung, sowie je Domänenmodul repräsentative
Happy-Path-/Fehlerpfad-Tests über `typer.testing.CliRunner` + `httpx.MockTransport` (kein echtes
Netzwerk). `uv run pytest tools/cli/tests`, `uv run ruff check tools/cli`.
