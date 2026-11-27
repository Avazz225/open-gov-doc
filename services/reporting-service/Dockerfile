# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package reporting-service --no-install-workspace
COPY libs/ libs/
COPY services/reporting-service/ services/reporting-service/

RUN uv sync --frozen --package reporting-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package reporting-service python -m dms_common.components --name reporting-service
LABEL org.opencontainers.image.title="reporting-service" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/reporting-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "reporting_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
