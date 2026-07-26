# Fortschritt

**Zuletzt abgeschlossen:** P0-S2 — Shared Libs, Service-Template, CI-Skeleton
**Nächste Session:** P1-S1 — Registry Service (Discovery, Heartbeat/Health, Routingtabelle)

Details je Session: siehe [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

## Session-Tabelle

| Session | Status | Deliverable |
|---|---|---|
| P0-S1 | ✅ fertig | Repo-Skeleton, Git-Init, Docker-Compose-Grundgerüst, graphify-Setup |
| P0-S2 | ✅ fertig | Shared Libs, Service-Template, CI-Skeleton |
| P1-S1 | offen | Registry Service |
| P1-S2 | offen | Event-Bus produktiv + Audit Service Grundgerüst |
| P2-S1 | offen | Keycloak + Auth Service |
| P2-S2 | offen | Permission Service (RBAC) |
| P3-S1 | offen | Storage Service + Backend-Plugin-Interface |
| P3-S2 | offen | Document Service |
| P3-S3 | offen | Folder Service + Object-Type Service + Constraint Engine |
| P3-S4 | offen | Storage-Redundanz + Bereichssperren |
| P4-S1 | offen | API-Gateway/BFF |
| P4-S2 | offen | User-UI Grundgerüst |
| P4-S3 | offen | Admin-UI Grundgerüst + Doku-Pass → **MVP-Meilenstein** |
| P5–P14 | offen | siehe IMPLEMENTATION_PLAN.md (30 weitere Sessions) |

## Offene Entscheidungen

- **Suche-Backend** (fällig spätestens P5-S4): Postgres Full-Text-Search vs. dedizierter Suchindex — noch nicht entschieden.
- **SpiffWorkflow-Lizenz** (fällig vor P6-S1): LGPLv3 vs. Open-Source-Ziele des Gesamtsystems — rechtliche Prüfung steht aus (Konzept 13).
- **Paketmanager `uv`**: verifiziert in P0-S2 — `uv sync --all-packages` als Standardbefehl für den gesamten Workspace etabliert (Einzel-Package-Sync via `--package` entfernt sonst Dev-Dependencies aus der venv, bewusst vermieden).

## Änderungslog

- **P0-S1**: Git-Repo in `dms/` initialisiert (Branch `main`). Monorepo-Skeleton angelegt (`services/`, `libs/`, `infra/`, `tools/cli/`, `docs/adr/`, `docs/services/`). `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `.gitignore` geschrieben. `infra/docker-compose.yml` mit Postgres 16, NATS 2.10 (JetStream), Keycloak 25.0, MinIO erstellt und **verifiziert** (`docker compose up` → alle vier Container healthy, Keycloak erfolgreich gegen eigenes Postgres-Schema migriert), danach wieder heruntergefahren. `graphify` über das Konzeptdokument laufen lassen, `graphify claude install` + `graphify hook install` ausgeführt. Nächster Schritt: P0-S2 (Shared Libs + Service-Template + CI).
- **P0-S2**: uv-Workspace-Root (`pyproject.toml`, `.python-version`, Ruff-Config inkl. `ignore = ["B008"]` für FastAPIs `Depends(...)`-Idiom) angelegt. Vier Shared Libs gebaut, jede mit README + **echten** Tests (16 Tests insgesamt, alle grün):
  - `dms-common`: `BaseServiceSettings`, JSON-Logging, OTel-Tracing-Basis.
  - `dms-db-base`: Async-Engine, `make_declarative_base(schema)`, Session-Scope — Integrationstest gegen echtes Postgres (Docker).
  - `dms-eventbus-client`: `EventBusClient`-Interface + `NatsEventBusClient` — Integrationstest gegen echtes NATS JetStream (Docker), inkl. Publish/Subscribe-Roundtrip.
  - `dms-auth-client`: `TokenValidator` (JWKS/RS256) + FastAPI-Dependency — Unit-Tests mit selbst signiertem Testschlüssel (kein echter Keycloak nötig).
  - Docker-Testinfrastruktur nach jedem Lib-Test wieder heruntergefahren.
  `docs/service-template.md` geschrieben (Layout, `pyproject.toml`, `main.py`, Dockerfile) — **Konvention festgelegt**: Docker-Images kopieren `libs/` aus dem Monorepo und installieren lokal via `uv sync --frozen --package <name>` (kein interner Package-Index, volle Reproduzierbarkeit über `uv.lock`), Build-Kontext ist die Repo-Wurzel. `.github/workflows/ci.yml` (Postgres als Service-Container, NATS separat gestartet wegen JetStream-Flag, `ruff check .` + `pytest` über den ganzen Workspace) — lokal durchgespielt, nicht auf echtem GitHub Actions verifiziert. `graphify dms/ --update`: 167 Knoten/209 Kanten/31 Communities (Konzeptdokument-Knoten wie geplant gepruned, da `Business__DMS-Konzept.md`/`CLAUDE.md` jetzt gitignored sind). Ein Self-Loop-Edge im Graph als kleine Health-Warnung notiert, unkritisch. Nächster Schritt: P1-S1 (Registry Service).
