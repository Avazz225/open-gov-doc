# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package object-type-service --no-install-workspace
COPY libs/ libs/
COPY services/object-type-service/ services/object-type-service/

RUN uv sync --frozen --package object-type-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package object-type-service python -m dms_common.components --name object-type-service
LABEL org.opencontainers.image.title="object-type-service" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/object-type-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "object_type_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
