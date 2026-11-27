# syntax=docker/dockerfile:1
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Third-party dependencies first, from the lock alone (P100-S1, ADR 0271): a change to
# libs/ or this service then reuses this layer instead of reinstalling everything.
RUN uv sync --frozen --package mail-connector --no-install-workspace
COPY libs/ libs/
COPY services/mail-connector/ services/mail-connector/

RUN uv sync --frozen --package mail-connector
# Components file (P100-S1, ADR 0271): every Python distribution and Debian package in
# this image, frozen at build time - readable without running the service.
RUN uv run --frozen --no-sync --package mail-connector python -m dms_common.components --name mail-connector
LABEL org.opencontainers.image.title="mail-connector" \
      dms.components="/app/META-INF/components.cdx.json"

WORKDIR /app/services/mail-connector
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "mail_connector.main:app", "--host", "0.0.0.0", "--port", "8000"]
