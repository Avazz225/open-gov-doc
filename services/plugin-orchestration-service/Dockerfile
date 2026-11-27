# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package plugin-orchestration-service --no-install-workspace
COPY libs/ libs/
COPY services/plugin-orchestration-service/ services/plugin-orchestration-service/

RUN uv sync --frozen --package plugin-orchestration-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package plugin-orchestration-service python -m dms_common.components --name plugin-orchestration-service
LABEL org.opencontainers.image.title="plugin-orchestration-service" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/plugin-orchestration-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "plugin_orchestration_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
