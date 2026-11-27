# Service template

Binding pattern for every new service under `services/<name>/`. Goal: new
services are created by copying this pattern, not by reinventing the
structure — consistent with the "add-on" principle (Concept 1.).

## Directory layout

```
services/<name>/
  src/<name>/
    __init__.py
    main.py           # FastAPI app factory, health endpoint
    settings.py        # <Name>Settings(BaseServiceSettings)
    ...                 # domain code
  tests/
  Dockerfile
  pyproject.toml
  README.md
```

## pyproject.toml

```toml
[project]
name = "<name>"
version = "0.1.0"
description = "..."
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "dms-common",
    "dms-db-base",
    "dms-eventbus-client",
    "dms-auth-client",
]

[tool.uv.sources]
dms-common = { workspace = true }
dms-db-base = { workspace = true }
dms-eventbus-client = { workspace = true }
dms-auth-client = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/<name>"]
```

Only list the libs the service actually needs (not every service necessarily
talks to the DB directly, e.g. pure gateway services).

## settings.py

```python
from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "<name>"
```

## main.py

```python
from contextlib import asynccontextmanager

from dms_common import configure_logging
from fastapi import FastAPI

from <name>.settings import Settings

settings = Settings()
configure_logging(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Registry registration (3.2), DB engine, event bus connect etc. go here.
    yield


app = FastAPI(title=settings.service_name, lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": settings.service_name}
```

## Dockerfile (self-contained: libs are copied from the monorepo and installed
locally, with no dependency on an internal package index)

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271).
RUN uv sync --frozen --package <name> --no-install-workspace
COPY libs/ libs/
COPY services/<name>/ services/<name>/

RUN uv sync --frozen --package <name>
# Components file (P100-S1, ADR 0271).
RUN uv run --frozen --no-sync --package <name> python -m dms_common.components --name <name>
LABEL org.opencontainers.image.title="<name>" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/<name>
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "<name>.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Layer order (P100-S1):** the third-party dependencies come from `uv.lock` before `libs/` is
copied, so a change to a library or to the service reuses that layer. A rebuild after a library change
takes about 10 s per image instead of minutes. Any `apt-get` step goes before the dependency step, so
its packages land in the components file.

**Components file:** `/app/META-INF/components.cdx.json` (CycloneDX 1.6: every Python distribution and
Debian package of the image), readable without running the service. `setup_observability` also
registers `GET /component-report` (for monitoring-service only) with the runtime view and its comparison
with this file.

**Important:** The Docker build context is the repo root (`dms/`), not the
service folder — otherwise `libs/` and `uv.lock` are not reachable for `COPY`.
In `infra/docker-compose.yml`:

```yaml
services:
  <name>:
    build:
      context: ..
      dockerfile: services/<name>/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - dms-net
```

This way, each image contains the exact lib code present in the repo at
build time, plus all third-party dependencies pinned via `uv.lock` — a later
update to a lib requires no manual bump of a registry version,
and a rebuild at any later point reproduces exactly the same state.

## Tests

`pytest` against the real infrastructure the service needs (Postgres/NATS via
`infra/docker-compose.yml`), analogous to the lib tests under `libs/*/tests`.

## Don't forget (Definition of Done, `../CONTRIBUTING.md`)

- Create `docs/services/<name>.md` (template in `docs/services/README.md`)
- Entry in `infra/docker-compose.yml`
- `graphify dms/ --update` at the end of the phase
