# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package license-service --no-install-workspace
COPY libs/ libs/
COPY services/license-service/ services/license-service/

RUN uv sync --frozen --package license-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package license-service python -m dms_common.components --name license-service
LABEL org.opencontainers.image.title="license-service" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/license-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "license_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
