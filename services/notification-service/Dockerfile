# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/notification-service/ services/notification-service/

RUN uv sync --frozen --package notification-service

WORKDIR /app/services/notification-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "notification_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
