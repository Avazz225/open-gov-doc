# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY libs/ libs/
COPY services/search-service/ services/search-service/
RUN uv sync --frozen --package search-service
WORKDIR /app/services/search-service
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "search_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
