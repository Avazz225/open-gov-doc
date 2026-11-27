# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# Vor dem COPY der eigenen Quellen, damit der apt-Layer bei Code-Änderungen
# gecacht bleibt. tesseract-ocr-deu zusätzlich zum Standardpaket, da dieses
# nur die englische Trainingsdatei mitbringt (Projekt ist deutschsprachig).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-deu \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package ocr-service --no-install-workspace
COPY libs/ libs/
COPY services/ocr-service/ services/ocr-service/
RUN uv sync --frozen --package ocr-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package ocr-service python -m dms_common.components --name ocr-service
LABEL org.opencontainers.image.title="ocr-service" \
      dms.components="/app/META-INF/components.cdx.json"
WORKDIR /app/services/ocr-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "ocr_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
