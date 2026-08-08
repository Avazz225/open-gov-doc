# Konventionen

Dieses Projekt wird über viele einzelne Arbeitssessions hinweg gebaut (siehe [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)). Diese Konventionen sorgen dafür, dass das Repo dabei durchgehend sauber und nachvollziehbar bleibt — nicht erst am Ende.

## Definition of Done (jede Session, die Code produziert)

1. Der betroffene Service/die Lib hat: `README.md`, Tests (`pytest`), `Dockerfile`, Eintrag in `infra/docker-compose.yml`, strukturiertes Logging.
2. `docs/services/<service>.md` existiert/ist aktuell (Verantwortung, Endpunkte, Schema, Events die er publiziert/konsumiert).
3. Nicht-triviale Architekturentscheidungen sind als kurzes ADR in `docs/adr/` festgehalten (Template siehe [`docs/adr/README.md`](docs/adr/README.md)).
4. `PROGRESS.md` ist aktualisiert: erledigte Session abgehakt, nächste Session benannt, offene Fragen notiert.
5. Bei substantiellem Code-Zuwachs: `graphify dms/ --update` (spätestens am Ende jeder Phase verpflichtend).
6. Tests laufen grün (`pytest` je Service, `docker compose up --build` startet fehlerfrei).

**Falle beim Docker-Smoke-Test**: `docker compose up -d` baut ein bereits existierendes Image **nicht** automatisch neu, auch wenn sich der Code geändert hat. Nach Code-Änderungen an einem Service vor dem Smoke-Test immer explizit `docker compose build <service>` (oder `up -d --build`) ausführen, sonst testet man versehentlich den alten Stand.

**Schema-Änderungen an einer bereits bestehenden Tabelle** (kein Alembic in dieser Projektphase, siehe oben): `Base.metadata.create_all` legt nur fehlende *Tabellen* an, ändert aber nie bestehende — eine neue Spalte an einer bereits existierenden Tabelle braucht zusätzlich eine idempotente `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DEFAULT ...`-Zeile im jeweiligen `main.py` (Expand-Schritt, Konzept 10.5, seit P7-S1 gelebte Praxis in `document-service`, formalisiert in [`docs/operations/rolling-updates.md`](docs/operations/rolling-updates.md)). Wird eine Spalte später wirklich überflüssig, gehört ihr Entfernen (Contract-Schritt) als eigener, separater Migrationsschritt in eine spätere Session, erst nachdem alle Instanzen aktualisiert sind — nie in denselben Schritt wie die Erweiterung.

**Tests laufen über `scripts/run-tests.sh`** (nicht mehr manuell Service für Service): bringt den Docker-Stack hoch, legt `dms_test` bei Bedarf an, exportiert `TEST_POSTGRES_DSN` explizit auf `dms_test` (der Default in den `conftest.py`-Dateien zeigt sonst auf die echte `dms`-Datenbank), stoppt vor dem Testlauf eines Services mit eigenem NATS-Konsumenten (`durable=<service-name>`) gezielt dessen Container und startet ihn danach wieder, und fasst am Ende alle Service-Ergebnisse plus `ruff check`/`ruff format --check` in einer Tabelle zusammen (Logs unter `.test-results/`). `scripts/run-tests.sh [service ...] [--build] [--no-ruff] [--down]` — ohne Argumente laufen alle Services mit `tests/`.

## Service-Aufbau

Verbindliches Muster (Layout, `pyproject.toml`, `main.py`, Dockerfile) siehe
[`docs/service-template.md`](docs/service-template.md) — neue Services entstehen
durch Kopieren dieses Musters.

Frontend-Anwendungen (Konzept 8) folgen diesem Python-Muster nicht — sie liegen
unter `apps/<name>/` statt `services/<name>/` (Next.js/statischer Export statt
`pyproject.toml`/`uv`), siehe [ADR 0006](docs/adr/0006-user-ui-static-export-spa.md).
Dieselbe Definition of Done gilt trotzdem, nur mit Node-typischem Tooling
(`npm run lint`/`test`/`build` statt `ruff`/`pytest`).

Ein Service liest/schreibt ausschließlich sein eigenes Postgres-Schema (Konzept 3.1) und kommuniziert mit anderen Services nur über deren API bzw. den Event-Bus — kein Import fremder Service-Interna. Docker-Images sind self-contained: `libs/` wird beim Image-Build aus dem Monorepo kopiert und lokal installiert (kein interner Package-Index), damit ein Rebuild zu jedem späteren Zeitpunkt denselben Stand reproduziert.

## Commits

- Ein Commit pro logischem Schritt, aussagekräftige Nachricht (was + warum, nicht nur was).
- Kein `--no-verify`, keine erzwungenen Pushes ohne ausdrückliche Rücksprache.
- `PROGRESS.md`-Update gehört in denselben oder den letzten Commit einer Session, nicht vergessen.

## Branching

Trunk-based auf `main`. Feature-Branches sind optional und bei Bedarf pro Session nutzbar, aber kein Zwang für dieses Solo-/Lern-Projekt.
