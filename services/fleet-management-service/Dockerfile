# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/fleet-management-service/ services/fleet-management-service/

RUN uv sync --frozen --package fleet-management-service

WORKDIR /app/services/fleet-management-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "fleet_management_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
