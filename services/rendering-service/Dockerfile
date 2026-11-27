# syntax=docker/dockerfile:1
FROM python:3.12-slim
# LibreOffice headless (5.6, seit P7-S3) - Office-/Text-Formate zu PDF/A
# konvertieren (PdfArchiveRenderer), damit auch .docx/.pptx/.xlsx/.odt/...
# aussonderungsfaehig sind, nicht nur bereits-PDF-Dokumente. Bewusst
# akzeptierte Image-Groessen-Zunahme fuer diese Formatabdeckung.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package rendering-service --no-install-workspace
COPY libs/ libs/
COPY services/rendering-service/ services/rendering-service/
RUN uv sync --frozen --package rendering-service
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package rendering-service python -m dms_common.components --name rendering-service
LABEL org.opencontainers.image.title="rendering-service" \
      dms.components="/app/META-INF/components.cdx.json"
WORKDIR /app/services/rendering-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "rendering_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
