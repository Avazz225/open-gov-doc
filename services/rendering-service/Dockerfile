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
COPY libs/ libs/
COPY services/rendering-service/ services/rendering-service/
RUN uv sync --frozen --package rendering-service
WORKDIR /app/services/rendering-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "rendering_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
