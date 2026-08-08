# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/plugin-orchestration-service/ services/plugin-orchestration-service/

RUN uv sync --frozen --package plugin-orchestration-service

WORKDIR /app/services/plugin-orchestration-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "plugin_orchestration_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
