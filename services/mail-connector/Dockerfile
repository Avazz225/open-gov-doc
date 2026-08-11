# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/mail-connector/ services/mail-connector/

RUN uv sync --frozen --package mail-connector

WORKDIR /app/services/mail-connector
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "mail_connector.main:app", "--host", "0.0.0.0", "--port", "8000"]
