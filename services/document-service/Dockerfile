# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
# libmagic1 für die Content-Type-Erkennung per Magic-Bytes (P5d-S1, python-magic).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/document-service/ services/document-service/

RUN uv sync --frozen --package document-service

WORKDIR /app/services/document-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "document_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
