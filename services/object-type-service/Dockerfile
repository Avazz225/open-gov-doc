# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/object-type-service/ services/object-type-service/

RUN uv sync --frozen --package object-type-service

WORKDIR /app/services/object-type-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "object_type_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
