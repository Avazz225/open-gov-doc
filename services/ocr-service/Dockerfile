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
COPY libs/ libs/
COPY services/ocr-service/ services/ocr-service/
RUN uv sync --frozen --package ocr-service
WORKDIR /app/services/ocr-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "ocr_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
