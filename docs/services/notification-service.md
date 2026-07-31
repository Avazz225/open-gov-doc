# notification-service

**Verantwortung:** Notification Service (Konzept 7.1) - E-Mail-, In-App- und Webhook-Benachrichtigungen. Konsumiert `workflow.task.escalated` von `workflow-service` (SLA-Zeitüberwachung, P6-S2) sowie seit P6-S5 `auth.superuser.activated` (Break-Glass-Sicherheitsbenachrichtigung, 4.6) und bietet zusätzlich einen generischen `POST /notifications`, den jeder Service direkt aufrufen kann.

**Konzept-Referenz:** 7.1
**Eigenes Postgres-Schema:** `notification` (Tabelle `notification`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/notifications` | Versand (`channel`: `email`\|`in_app`\|`webhook`, `recipient`, `subject`, `body`) - persistiert zuerst, versucht dann synchron zuzustellen. **`201` immer**, das tatsächliche Ergebnis steht im `status`-Feld (`sent`\|`failed`), kein HTTP-Fehlercode bei fehlgeschlagener Zustellung |
| `GET` | `/notifications?recipient=&channel=&status=` | Gefilterte Liste |
| `GET` | `/notifications/{id}` | Detail - `404` |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

`notification`: `id`, `channel` (`"email"`\|`"in_app"`\|`"webhook"`), `recipient` (heterogen je Kanal: E-Mail-Adresse / Lane-Name-oder-Nutzerkennung / Webhook-URL - bewusst ein einziges generisches Feld statt dreier kanalspezifischer Spalten), `subject`, `body`, `status` (`"sent"`\|`"failed"`), `error` (nullable), `created_at`, `sent_at` (nullable).

## Kanäle (`src/notification_service/delivery.py`)

- **E-Mail**: SMTP via `aiosmtplib`, gegen die installierte Version (5.1.2) per `inspect` verifiziert. Dev-Standard zeigt auf den `mailpit`-Container (`infra/docker-compose.yml`, Web-UI unter `MAILPIT_UI_PORT`) - kein Auth nötig. Für echten SMTP-Betrieb `smtp_username`/`smtp_password`/`smtp_use_tls` setzen.
- **In-App**: reine Persistenz, sofort `status="sent"`. Kein UI diese Session (Abfrage nur über `GET /notifications`) - Konzept 7.1 nennt In-App als Kanal, eine UI-Anzeige ist nicht Teil von P6-S2.
- **Webhook**: HTTP-POST via `httpx` an die als `recipient` übergebene URL, JSON-Payload `{"subject": ..., "body": ...}`, Timeout 5s.

Zustellung passiert **synchron** beim Anlegen des Datensatzes, Ergebnis landet direkt am selben Datensatz. **Kein Retry** - ein einzelner fehlgeschlagener Versuch bleibt als `"failed"` stehen (siehe ADR 0020 und "Offene Punkte").

## `workflow.task.escalated`-Konsument (`src/notification_service/consumer.py`)

Abonniert **gezielt** `workflow.task.escalated` (nicht `workflow.>` - `workflow.instance.*`/`workflow.task.completed` haben keine Benachrichtigungs-Semantik). Pro Event:

1. Legt **immer** eine In-App-Notification an (`recipient` = `lane`-Wert aus dem Event-Payload, sonst `"unassigned"` - keine Rollen-Auflösung ohne RBAC, siehe "Offene Punkte").
2. Legt **zusätzlich** eine E-Mail-Notification an, falls das Payload einen `escalation_email`-Wert enthält (opakes, unvalidiertes Prozessdatum aus `initial_data` beim Instanzstart, Konvention wie `business_key` in `workflow-service`).

Nach jeder Zustellung wird `notification.sent`/`notification.failed` publiziert.

## `auth.superuser.activated`-Konsument (seit P6-S5, 4.6)

Zweiter Zweig desselben `consumer.py`-Handlers, dispatcht nach `event.event_type` statt nach Payload-Feldern (anders als der SLA-Zweig, der keinen eigenen `event_type`-Vergleich braucht, da bislang nur ein Subject konsumiert wurde). Legt eine **einzelne** E-Mail-Notification an `settings.security_officer_email` an (fest konfiguriert, kein Empfänger-Auflösungsmechanismus wie bei `escalation_email` nötig) — Umsetzung der in 4.6 als "optional" beschriebenen Sicherheitsbenachrichtigung bei Break-Glass-Aktivierung.

## Events

**Publiziert** (Stream `notification`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `notification.sent` | `{channel, recipient}` |
| `notification.failed` | `{channel, recipient, error}` |

**Konsumiert** (`durable="notification-service"`): `workflow.task.escalated` (aus `workflow-service`), seit P6-S5 zusätzlich `auth.superuser.activated` (aus `auth-service`, siehe `docs/services/auth-service.md`).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry, identisches Muster wie jeder andere Service.

## Sensoren (Konzept 10.1)

Noch keine - folgt in Phase 11.

## Tests

`uv run pytest services/notification-service/tests` - läuft gegen eine echte Postgres-Instanz und (für die E-Mail-Pfade) gegen den echten `mailpit`-Container, kein Mocking:
- `test_delivery.py` - E-Mail real gegen `mailpit`, Webhook gegen einen lokal in der Testsuite gestarteten `http.server`, jeweils Erfolgs- und Fehlerfall (unerreichbarer SMTP-Server/unerreichbare URL).
- `test_repository.py` - `create_and_send` inkl. Persistenz des Fehlerfalls, Filterung nach `recipient`/`channel`.
- `test_api.py` - alle Endpunkte inkl. `404`.
- `test_consumer.py` - simuliertes `workflow.task.escalated`-Event (direkt an `consumer.make_handler`, ohne echtes NATS) erzeugt die erwarteten In-App-/E-Mail-Notifications, inkl. Fall ohne `escalation_email` (nur In-App).
- Reine Backend-Session, kein Browser-Test nötig.

## Offene Punkte

- **Keine Rollenprüfung/RBAC** - `POST /notifications` ist ungated aufrufbar, `recipient` ist ein unvalidierter String. Explizit **P6-S4** zugewiesen, gleiches Muster wie `workflow-service` seit P6-S1.
- **Keine Empfänger-Auflösung über Rollen** - eine BPMN-Lane wird nur informativ als `recipient` für In-App-Notifications verwendet, nicht gegen echte Nutzerkonten/Rollen in `auth-service` aufgelöst (dort existiert aktuell auch keine Rollen-Abfrage für Nutzer). Sobald RBAC (P6-S4) steht, könnte eine "Vorgesetzten-Rolle" echten E-Mail-Adressen zugeordnet werden, statt sich auf das opake `escalation_email`-Prozessdatum zu verlassen.
- **Kein Retry/keine Dead-Letter-Behandlung** - ein fehlgeschlagener Zustellversuch (SMTP/Webhook nicht erreichbar) bleibt dauerhaft `"failed"`, es gibt keinen automatischen erneuten Versuch. Offener Punkt für eine spätere Session, falls das operativ relevant wird.
- **Kein Retrofit bestehender "loggt nur"-Alarmierungsstellen** - `storage-service` (und andere, in Konzept an verschiedenen Stellen erwähnte künftige Konsumenten wie Force-Unlock, Löschfrist-Vorankündigung, Lizenz-Ablauf, Report-Versand, Monitoring-Eskalation) bleiben **nicht** auf den Service umgehängt. Break-Glass (4.6) ist seit P6-S5 die erste Ausnahme (siehe oben). Jeder künftige Konsument trägt sein Subject selbst in `settings.py`s `subjects`-Liste ein, sobald er tatsächlich angebunden wird.
- **Ein Notification-Datensatz je Kanal, kein Multi-Channel-Fan-out aus einem Aufruf** - wer eine Eskalation über mehrere Kanäle gleichzeitig verteilen will (z. B. E-Mail und Webhook), muss `POST /notifications` mehrfach aufrufen. Der `workflow.task.escalated`-Konsument selbst deckt genau den in Konzept 7.1 beschriebenen Fall ab (immer In-App, optional zusätzlich E-Mail).
- **Kein Rate-Limiting/Spam-Schutz** - ein Prozess mit sehr kurzem, wiederholt feuerndem Cycle-Timer (nicht getestet diese Session, siehe `docs/services/workflow-service.md` "Offene Punkte") könnte denselben Empfänger wiederholt benachrichtigen.
