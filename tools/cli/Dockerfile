# syntax=docker/dockerfile:1
# Kein Dienst (kein Health-Endpoint, kein "up"-Zustand) - daher kein Eintrag
# in infra/docker-compose.yml, siehe docs/tools/cli.md. Dieses Image dient
# CI/CD-Pipelines, die kein lokales Python/uv installieren wollen:
#   docker run --rm -e DMS_GATEWAY_URL=... -e DMS_TOKEN=... dms-cli:latest query events list
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY tools/cli/ tools/cli/

RUN uv sync --frozen --package dms-cli

WORKDIR /app/tools/cli
ENTRYPOINT ["uv", "run", "dms"]
