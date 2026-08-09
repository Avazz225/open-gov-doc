# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/config-service/ services/config-service/

RUN uv sync --frozen --package config-service

WORKDIR /app/services/config-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "config_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
