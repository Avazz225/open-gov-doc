# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/virus-scan-service/ services/virus-scan-service/

RUN uv sync --frozen --package virus-scan-service

WORKDIR /app/services/virus-scan-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "virus_scan_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
