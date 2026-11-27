# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
# libmagic1 für die Content-Type-Erkennung per Magic-Bytes (P5d-S1, python-magic).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package document-service --no-install-workspace
COPY libs/ libs/
COPY services/document-service/ services/document-service/

RUN uv sync --frozen --package document-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package document-service python -m dms_common.components --name document-service
LABEL org.opencontainers.image.title="document-service" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/document-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "document_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
