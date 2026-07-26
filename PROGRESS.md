# Fortschritt

**Zuletzt abgeschlossen:** P0-S1 — Repo-Skeleton, Git-Init, Docker-Compose-Basisinfrastruktur, graphify-Setup
**Nächste Session:** P0-S2 — Shared Libs (`dms-common`, `dms-db-base`, `dms-eventbus-client`, `dms-auth-client`), Service-Template, CI-Skeleton

Details je Session: siehe [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md).

## Session-Tabelle

| Session | Status | Deliverable |
|---|---|---|
| P0-S1 | ✅ fertig | Repo-Skeleton, Git-Init, Docker-Compose-Grundgerüst, graphify-Setup |
| P0-S2 | offen | Shared Libs, Service-Template, CI-Skeleton |
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
- **Paketmanager `uv`**: als Default gesetzt in P0-S1, noch nicht in P0-S2 mit echtem Service verifiziert.

## Änderungslog

- **P0-S1**: Git-Repo in `dms/` initialisiert (Branch `main`). Monorepo-Skeleton angelegt (`services/`, `libs/`, `infra/`, `tools/cli/`, `docs/adr/`, `docs/services/`). `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `.gitignore` geschrieben. `infra/docker-compose.yml` mit Postgres 16, NATS 2.10 (JetStream), Keycloak 25.0, MinIO erstellt und **verifiziert** (`docker compose up` → alle vier Container healthy, Keycloak erfolgreich gegen eigenes Postgres-Schema migriert), danach wieder heruntergefahren. `graphify` über das Konzeptdokument laufen lassen, `graphify claude install` + `graphify hook install` ausgeführt. Nächster Schritt: P0-S2 (Shared Libs + Service-Template + CI).
