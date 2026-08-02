# notification-service

**Verantwortung:** Notification Service (Konzept 7.1) - E-Mail-, In-App- und Webhook-Benachrichtigungen. Konsumiert `workflow.task.escalated` von `workflow-service` (SLA-Zeitüberwachung, P6-S2) sowie seit P6-S5 `auth.superuser.activated` (Break-Glass-Sicherheitsbenachrichtigung, 4.6), seit P6-S6 `permission.maintenance_mode.activated` (Not-Shutdown-Sicherheitsbenachrichtigung, 4.8) und seit P6-S9 `workflow.federation.inbound_received` (Federation Hub, 7.4) und bietet zusätzlich einen generischen `POST /notifications`, den jeder Service direkt aufrufen kann. Seit **P6-S6** prüft dieser Endpunkt die Empfänger-Existenz gegen echte `auth-service`-Konten und bleibt bewusst auch während des systemweiten Wartungsmodus erreichbar (wird für die Alarmierung selbst gebraucht) — siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Konzept-Referenz:** 7.1, 4.8
**Eigenes Postgres-Schema:** `notification` (Tabelle `notification`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/notifications` | Versand (`channel`: `email`\|`in_app`\|`webhook`, `recipient`, `subject`, `body`) - persistiert zuerst, versucht dann synchron zuzustellen. **`201` immer**, das tatsächliche Ergebnis steht im `status`-Feld (`sent`\|`failed`), kein HTTP-Fehlercode bei fehlgeschlagener Zustellung. **Seit P6-S6**: für `channel` `email`/`in_app` wird `recipient` zuerst gegen echte `auth-service`-Konten geprüft (`GET /users`) — `400` bei unbekanntem Empfänger; `webhook` bleibt ungeprüft (Ziel ist eine URL, keine Identität) |
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

## `permission.maintenance_mode.activated`-Konsument (seit P6-S6, 4.8)

Dritter Zweig desselben `consumer.py`-Handlers, identisches Dispatch-Prinzip wie der Break-Glass-Zweig — legt eine einzelne E-Mail-Notification an `settings.security_officer_email` an ("Systemweite Notfallsperre ausgelöst"). Dieser Konsument läuft unverändert weiter, **auch während der systemweite Wartungsmodus selbst aktiv ist** — der Service ist dafür bewusst nicht Teil der Gateway-Blockade (siehe unten), sonst könnte er genau die Alarmierung nicht zustellen, die 4.8 für seine eigene Aktivierung verlangt.

## `workflow.federation.inbound_received`-Konsument (seit P6-S9, 7.4)

Vierter Zweig desselben `consumer.py`-Handlers — Benachrichtigung der Zielinstallation bei einer eingehenden föderierten Übergabe über den Federation Hub (siehe `docs/services/workflow-service.md` "Federation"). Gleiches `notify_email`-Muster wie beim SLA-Zweig: immer eine In-App-Notification (Empfänger `"unassigned"`, da es für einen frisch von außen gestarteten Prozess keinen Lane-Namen gibt), zusätzlich eine E-Mail, falls die Absenderseite ein `notify_email`-Prozessdatum mitgegeben hat.

**Echter Bug gefunden und behoben**: `workflow.federation.inbound_received` teilt sich den `"workflow"`-Stream mit dem bereits bestehenden `workflow.task.escalated` (P6-S2). Ein durable JetStream-Konsumentenname ist pro **Stream**, nicht pro Subject eindeutig — ein zweiter `subscribe()`-Aufruf mit demselben Durable-Namen `"notification-service"`, aber einem anderen Filter-Subject auf demselben Stream, schlägt mit `"consumer is already bound to a subscription"` fehl (reproduzierbar bei jedem Neustart/Testlauf). Fix in `start_consuming()`: das neue Subject bekommt einen eigenen, zweiten Durable-Namen (`"notification-service-federation"`) — die drei bereits bestehenden Subjects behalten ihren ursprünglichen Namen (keine Neuzustellung von deren bisherigem Verlauf).

## Empfänger-Existenzprüfung (`POST /notifications`, seit P6-S6, 4.8-Retrofit)

Neuer, dünner `auth_client.py`: `recipient_exists(recipient, channel)` — für `channel="webhook"` immer `True` (Ziel ist eine URL, keine Identität), sonst `GET /users` am Auth Service und Abgleich gegen `username`/`email`. Da `GET /users` seit P6-S5 hinter der Capability `admin.user_management` gegated ist, authentifiziert sich `notification-service` dafür als das bestehende technische Konto `users-admin` (`POST /login` bei **jedem** Aufruf, kein Token-Caching — akzeptierter Latenz-Trade-off für ein niedrigfrequentes internes Prüfen, kein drittes technisches Konto eingeführt). `POST /notifications` lehnt einen unbekannten Empfänger für `email`/`in_app` mit `400` ab, **bevor** `repository.create_and_send` aufgerufen wird — der `workflow.task.escalated`-Konsument und die beiden Sicherheitsbenachrichtigungs-Konsumenten (`auth.superuser.activated`, `permission.maintenance_mode.activated`) rufen `repository.create_and_send` weiterhin direkt auf, nicht über diesen HTTP-Endpunkt, und sind von der Prüfung daher unberührt (siehe "Empfänger-Auflösung" in den Offenen Punkten).

## Erreichbarkeit während des Wartungsmodus (4.8, seit P6-S6)

`notification-service` ist **bewusst nicht** in der Gateway-Allow-Liste für den Wartungsmodus enthalten, aber auch nicht davon betroffen: Die Gateway-Sperre blockiert nur *proxied* Requests an Backends von außen; `notification-service` selbst empfängt seine Not-Shutdown-Alarmierung über NATS (s. o.), nicht über einen proxied HTTP-Aufruf. `POST /notifications` direkt am Gateway aufgerufen wäre während einer aktiven Sperre wie jeder andere Endpunkt blockiert — nur der interne Event-Konsum bleibt in jedem Fall funktionsfähig. Siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md) für die vollständige Begründung.

## Events

**Publiziert** (Stream `notification`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `notification.sent` | `{channel, recipient}` |
| `notification.failed` | `{channel, recipient, error}` |

**Konsumiert** (`durable="notification-service"`): `workflow.task.escalated` (aus `workflow-service`), seit P6-S5 zusätzlich `auth.superuser.activated` (aus `auth-service`), seit P6-S6 zusätzlich `permission.maintenance_mode.activated` (aus `permission-service`, siehe `docs/services/permission-service.md`), seit P6-S9 zusätzlich `workflow.federation.inbound_received` (aus `workflow-service`, siehe `docs/services/workflow-service.md` "Federation").

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry, identisches Muster wie jeder andere Service.

## Sensoren (Konzept 10.1)

Noch keine - folgt in Phase 11.

## Tests

`uv run pytest services/notification-service/tests` - läuft gegen eine echte Postgres-Instanz und (für die E-Mail-Pfade) gegen den echten `mailpit`-Container, kein Mocking:
- `test_delivery.py` - E-Mail real gegen `mailpit`, Webhook gegen einen lokal in der Testsuite gestarteten `http.server`, jeweils Erfolgs- und Fehlerfall (unerreichbarer SMTP-Server/unerreichbare URL).
- `test_repository.py` - `create_and_send` inkl. Persistenz des Fehlerfalls, Filterung nach `recipient`/`channel`.
- `test_api.py` - alle Endpunkte inkl. `404`.
- `test_consumer.py` - simuliertes `workflow.task.escalated`-Event (direkt an `consumer.make_handler`, ohne echtes NATS) erzeugt die erwarteten In-App-/E-Mail-Notifications, inkl. Fall ohne `escalation_email` (nur In-App); seit **P6-S6** zusätzlich ein simuliertes `permission.maintenance_mode.activated`-Event erzeugt die Sicherheitsbenachrichtigung an `security_officer_email`; seit **P6-S9** zusätzlich ein simuliertes `workflow.federation.inbound_received`-Event mit/ohne `notify_email` (In-App+E-Mail bzw. nur In-App, gleiches Muster wie beim SLA-Zweig).
- Seit **P6-S6** zusätzlich: `test_api.py` nutzt ein neues `real_recipient`-Fixture (legt real einen Nutzer über den live laufenden `auth-service` an, `users-admin`-Login) für die Erfolgsfälle, sowie eigene Tests für `400` bei unbekanntem Empfänger (`email`/`in_app`) — kein Mocking von `auth-service`.
- **22 Tests** (vorher 18, 4 neu: siehe oben).
- Reine Backend-Session, kein Browser-Test nötig.

## Offene Punkte

- **Rollenprüfung seit P6-S6 nur als Empfänger-Existenzprüfung** — `POST /notifications` verlangt weiterhin keine Berechtigung des *Aufrufers*, nur dass der angegebene `recipient` (für `email`/`in_app`) ein echtes `auth-service`-Konto ist. Jeder authentifizierte Principal kann weiterhin für jeden bekannten Empfänger eine Notification auslösen — keine Rollenprüfung des Aufrufers selbst, siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md) (Nutzerentscheidung, engerer Retrofit-Umfang).
- **Keine Empfänger-Auflösung über Rollen** - eine BPMN-Lane wird nur informativ als `recipient` für In-App-Notifications verwendet, nicht gegen echte Nutzerkonten/Rollen in `auth-service` aufgelöst (dort existiert aktuell auch keine Rollen-Abfrage für Nutzer). Eine "Vorgesetzten-Rolle" könnte künftig echten E-Mail-Adressen zugeordnet werden, statt sich auf das opake `escalation_email`-Prozessdatum zu verlassen — weiterhin nicht Teil dieser Session.
- **Technisches Konto `users-admin` dient seit P6-S6 auch als interne Service-Anmeldung** — `notification-service` authentifiziert sich für die Empfänger-Prüfung als fremdes technisches Konto statt einer eigenen Identität; ein wiederholtes Auftreten dieses Musters bei weiterem Wachstum sollte revisitiert werden (siehe ADR 0024 "Konsequenzen").
- **Kein Retry/keine Dead-Letter-Behandlung** - ein fehlgeschlagener Zustellversuch (SMTP/Webhook nicht erreichbar) bleibt dauerhaft `"failed"`, es gibt keinen automatischen erneuten Versuch. Offener Punkt für eine spätere Session, falls das operativ relevant wird.
- **Kein Retrofit bestehender "loggt nur"-Alarmierungsstellen** - `storage-service` (und andere, in Konzept an verschiedenen Stellen erwähnte künftige Konsumenten wie Force-Unlock, Löschfrist-Vorankündigung, Lizenz-Ablauf, Report-Versand, Monitoring-Eskalation) bleiben **nicht** auf den Service umgehängt. Break-Glass (4.6) ist seit P6-S5 die erste Ausnahme (siehe oben). Jeder künftige Konsument trägt sein Subject selbst in `settings.py`s `subjects`-Liste ein, sobald er tatsächlich angebunden wird.
- **Ein Notification-Datensatz je Kanal, kein Multi-Channel-Fan-out aus einem Aufruf** - wer eine Eskalation über mehrere Kanäle gleichzeitig verteilen will (z. B. E-Mail und Webhook), muss `POST /notifications` mehrfach aufrufen. Der `workflow.task.escalated`-Konsument selbst deckt genau den in Konzept 7.1 beschriebenen Fall ab (immer In-App, optional zusätzlich E-Mail).
- **Kein Rate-Limiting/Spam-Schutz** - ein Prozess mit sehr kurzem, wiederholt feuerndem Cycle-Timer (nicht getestet diese Session, siehe `docs/services/workflow-service.md` "Offene Punkte") könnte denselben Empfänger wiederholt benachrichtigen.
