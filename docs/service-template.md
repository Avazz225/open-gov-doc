# Service-Template

Verbindliches Muster für jeden neuen Service unter `services/<name>/`. Ziel: neue
Services entstehen durch Kopieren dieses Musters, nicht durch Neuerfinden der
Struktur — passt zum "Dazustellen"-Prinzip (Konzept 1.).

## Verzeichnislayout

```
services/<name>/
  src/<name>/
    __init__.py
    main.py           # FastAPI-App-Factory, Health-Endpoint
    settings.py        # <Name>Settings(BaseServiceSettings)
    ...                 # fachlicher Code
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

Nur die Libs eintragen, die der Service tatsächlich braucht (nicht jeder Service
spricht zwingend die DB direkt an, z. B. reine Gateway-Services).

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
    # Registry-Registrierung (3.2), DB-Engine, Event-Bus-Connect etc. hier.
    yield


app = FastAPI(title=settings.service_name, lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": settings.service_name}
```

## Dockerfile (self-contained: Libs werden aus dem Monorepo kopiert und lokal
installiert, keine Abhängigkeit von einem internen Package-Index)

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/<name>/ services/<name>/

RUN uv sync --frozen --package <name>

WORKDIR /app/services/<name>
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "<name>.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Wichtig:** Der Docker-Build-Kontext ist die Repo-Wurzel (`dms/`), nicht der
Service-Ordner — sonst sind `libs/` und `uv.lock` für `COPY` nicht erreichbar.
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

Dadurch enthält jedes Image den exakten, zum Build-Zeitpunkt im Repo vorhandenen
Lib-Code plus alle Fremdabhängigkeiten fixiert über `uv.lock` — ein späteres
Update einer Lib erfordert kein manuelles Nachziehen einer Registry-Version,
und ein Rebuild zu einem beliebigen späteren Zeitpunkt reproduziert exakt denselben Stand.

## Tests

`pytest` gegen die im Service benötigte reale Infrastruktur (Postgres/NATS via
`infra/docker-compose.yml`), analog zu den Lib-Tests unter `libs/*/tests`.

## Nicht vergessen (Definition of Done, `../CONTRIBUTING.md`)

- `docs/services/<name>.md` anlegen (Template in `docs/services/README.md`)
- Eintrag in `infra/docker-compose.yml`
- `graphify dms/ --update` am Ende der Phase
