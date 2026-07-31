# notification-service

Notification Service (Konzept 7.1): E-Mail-, In-App- und Webhook-Benachrichtigungen.
Konsumiert `workflow.task.escalated` von `workflow-service` (SLA-Zeitüberwachung,
P6-S2, siehe [ADR 0020](../../docs/adr/0020-sla-timer-polling.md)) und bietet zusätzlich
einen generischen `POST /notifications`, den jeder Service direkt aufrufen kann.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/notifications` | Versand (`channel`: `email`\|`in_app`\|`webhook`, `recipient`, `subject`, `body`) - `201` immer, Ergebnis (`status`: `sent`\|`failed`) im Body |
| `GET` | `/notifications?recipient=&channel=&status=` | Gefilterte Liste |
| `GET` | `/notifications/{id}` | Detail |
| `GET` | `/healthz` | Health-Check |

Details/Schema/Events/Offene Punkte: siehe `../../docs/services/notification-service.md`.

## Kanäle

- **E-Mail**: SMTP via `aiosmtplib`, Dev-Standard gegen den `mailpit`-Container
  (`infra/docker-compose.yml`, Web-UI unter `:8025`).
- **In-App**: reine Persistenz, kein UI diese Session - Abfrage über `GET /notifications`.
- **Webhook**: HTTP-POST via `httpx` an die als `recipient` übergebene URL.

Zustellung passiert synchron beim Anlegen, kein Retry - siehe `src/notification_service/delivery.py`.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats registry-service mailpit notification-service
curl localhost:8015/healthz
```

## Tests

```bash
uv run pytest services/notification-service/tests
```

Läuft gegen eine echte Postgres-Instanz und (für die E-Mail-Tests) gegen `mailpit`
(kein Mocking) - wie bei jedem anderen Service niemals gegen die laufende
Entwicklungs-Datenbank, siehe `PROGRESS.md` "Tooling & Testing".
