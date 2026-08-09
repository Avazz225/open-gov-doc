# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/webdav-connector/ services/webdav-connector/

RUN uv sync --frozen --package webdav-connector

WORKDIR /app/services/webdav-connector
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "webdav_connector.main:app", "--host", "0.0.0.0", "--port", "8000"]
