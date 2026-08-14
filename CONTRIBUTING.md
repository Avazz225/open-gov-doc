# Conventions

This project is built across many individual work sessions (see [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)). These conventions ensure the repo stays clean and traceable throughout — not just at the end.

## Definition of Done (every session that produces code)

1. The affected service/lib must have: `README.md`, tests (`pytest`), `Dockerfile`, an entry in `infra/docker-compose.yml`, structured logging.
2. `docs/services/<service>.md` must exist and be up to date (responsibility, endpoints, schema, events it publishes/consumes).
3. Non-trivial architecture decisions must be recorded as a short ADR in `docs/adr/` (see [`docs/adr/README.md`](docs/adr/README.md) for the template).
4. `PROGRESS.md` must be updated: completed session checked off, next session named, open questions noted.
5. `graphify dms/ --update`: run this **only at the end of a complete phase** (after the last `PX-Sy` session of that phase per `IMPLEMENTATION_PLAN.md`), not after every individual session — a generalized standing rule (originally introduced for Phase 13, confirmed as a general project convention since P14-S1).
6. Tests must pass (`pytest` per service, `docker compose up --build` must start without errors).

**Pitfall in the Docker smoke test**: `docker compose up -d` does **not** automatically rebuild an already-existing image, even if the code has changed. After code changes to a service, always explicitly run `docker compose build <service>` (or `up -d --build`) before the smoke test, or you will inadvertently test the old state.

**Schema changes to an already-existing table** (no Alembic at this project stage, see above): `Base.metadata.create_all` only creates missing *tables*, it never alters existing ones — a new column on an already-existing table additionally requires an idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DEFAULT ...` line in the respective `main.py` (expand step, Concept 10.5, established practice in `document-service` since P7-S1, formalized in [`docs/operations/rolling-updates.md`](docs/operations/rolling-updates.md)). If a column later genuinely becomes obsolete, its removal (contract step) must be its own, separate migration step in a later session, only after all instances have been updated — never in the same step as the addition.

**Tests run via `scripts/run-tests.sh`** (no longer manually service by service): it brings up the Docker stack, creates `dms_test` if needed, explicitly exports `TEST_POSTGRES_DSN` pointing to `dms_test` (the default in the `conftest.py` files otherwise points to the real `dms` database), specifically stops the container of any service with its own NATS consumer (`durable=<service-name>`) before that service's test run and restarts it afterward, and at the end summarizes all service results plus `ruff check`/`ruff format --check` in a table (logs under `.test-results/`). `scripts/run-tests.sh [service ...] [--build] [--no-ruff] [--down]` — without arguments, all services with a `tests/` directory run.

## Service structure

For the binding pattern (layout, `pyproject.toml`, `main.py`, Dockerfile) see
[`docs/service-template.md`](docs/service-template.md) — new services must be created
by copying this pattern.

Frontend applications (Concept 8) do not follow this Python pattern — they live
under `apps/<name>/` instead of `services/<name>/` (Next.js/static export instead of
`pyproject.toml`/`uv`), see [ADR 0006](docs/adr/0006-user-ui-static-export-spa.md).
The same Definition of Done still applies, just with Node-typical tooling
(`npm run lint`/`test`/`build` instead of `ruff`/`pytest`).

A service must read/write only its own Postgres schema (Concept 3.1) and must communicate with other services only via their API or the event bus — no importing another service's internals. Docker images must be self-contained: `libs/` is copied from the monorepo and installed locally at image build time (no internal package index), so that a rebuild at any later point reproduces the same state.

## Commits

- One commit per logical step, with a meaningful message (what + why, not just what).
- No `--no-verify`, and no forced pushes without explicit prior agreement.
- The `PROGRESS.md` update belongs in the same commit as, or the last commit of, a session — do not forget it.

## Branching

Trunk-based on `main`. Feature branches are optional and may be used per session as needed, but are not mandatory for this solo/learning project.
