# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/archival-service/ services/archival-service/

RUN uv sync --frozen --package archival-service

WORKDIR /app/services/archival-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "archival_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
