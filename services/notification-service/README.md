# notification-service

Notification Service (Concept 7.1): email, in-app, and webhook notifications.
Consumes `workflow.task.escalated` from `workflow-service` (SLA time monitoring,
P6-S2, see [ADR 0020](../../docs/adr/0020-sla-timer-polling.md)) and additionally
offers a generic `POST /notifications` that any service can call directly.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/notifications` | Send (`channel`: `email`\|`in_app`\|`webhook`, `recipient`, `subject`, `body`) - always `201`, result (`status`: `sent`\|`failed`) in the body |
| `GET` | `/notifications?recipient=&channel=&status=` | Filtered list |
| `GET` | `/notifications/{id}` | Detail |
| `GET` | `/healthz` | Health check |

Details/schema/events/open items: see `../../docs/services/notification-service.md`.

## Channels

- **Email**: SMTP via `aiosmtplib`, dev default against the `mailpit` container
  (`infra/docker-compose.yml`, web UI at `:8025`).
- **In-app**: pure persistence, no UI this session - queried via `GET /notifications`.
- **Webhook**: HTTP POST via `httpx` to the URL passed as `recipient`.

Delivery happens synchronously on creation, no retry - see `src/notification_service/delivery.py`.

## Running locally

```bash
cd infra && docker compose up -d postgres nats registry-service mailpit notification-service
curl localhost:8015/healthz
```

## Tests

```bash
uv run pytest services/notification-service/tests
```

Runs against a real Postgres instance and (for the email tests) against `mailpit`
(no mocking) - like every other service, never against the running
development database, see `PROGRESS.md` "Tooling & Testing".
