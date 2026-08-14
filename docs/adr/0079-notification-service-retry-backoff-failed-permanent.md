# 0079 — notification-service: Retry/Backoff, `failed_permanent`, asynchroner Retry-Poll-Loop

**Status:** akzeptiert (Session 3 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 3, betrifft `notification-service`

## Entscheidung

`Notification` hatte bislang **gar keinen Retry-Mechanismus**: ein fehlgeschlagener `email`/`webhook`-
Zustellversuch setzte sofort `status="failed"` (terminal) — bereits als offener Punkt in
`docs/services/notification-service.md` dokumentiert. Diese Session schließt die Lücke mit dem in
P20-S1 (`libs/dms-retry`) angelegten Backoff-Baustein, adaptiert an die Besonderheit dieses Service:
**Zustellung passiert synchron inline im NATS-Handler bzw. in `POST /notifications`** (kein
mehrphasiger Prozess wie bei `archival-service`, ADR 0078) — ein Wiederholungsversuch darf diesen
Pfad nicht blockieren.

1. **Neue Felder** `attempts: int` (Default 0) und `next_retry_at: datetime | None` auf `Notification`.
2. **`attempt_delivery`** (vormals `create_and_send`s inline Try/Except, jetzt eine eigene, wiederverwendbare
   Funktion): bei Erfolg `status="sent"`; bei einem `DeliveryError` unterhalb von
   `Settings.max_notification_attempts` (Default 5) bleibt `status="failed"` (retry-fähig) mit einem per
   `compute_backoff_seconds` gesetzten `next_retry_at`. Erst bei Erschöpfung wechselt `status` auf das
   neue Terminalstatus `failed_permanent`. `in_app` hat keinen echten Zustellschritt und ist daher nie
   retry-fähig — immer sofort `"sent"`.
3. **Neuer, eigenständiger `_notification_retry_poll_loop`** (main.py, Intervall
   `notification_retry_poll_interval_seconds`, Default 60s — deutlich kürzer als z. B.
   `archival_poll_interval_seconds`, da eine E-Mail-/Webhook-Zustellung typischerweise binnen Sekunden
   bis Minuten erneut sinnvoll ist, nicht Stunden): greift über `list_due_for_retry` fällige
   `"failed"`-Notifications auf, ruft erneut `attempt_delivery` auf und publiziert das Ergebnis
   (`notification.sent`/`.failed`) — der ERSTE Versuch bleibt synchron im Handler, nur die WIEDERHOLUNG
   läuft asynchron. Die eigentliche Tick-Logik ist in `_run_retry_tick` ausgelagert (isoliert testbar,
   gleiches Muster wie `archival-service`s `run_active_transfers_tick`).
4. **Neuer Endpunkt** `POST /notifications/{id}/retry`: `409` wenn `status != "failed_permanent"`, sonst
   `repository.retry_now` — setzt `attempts=0`/`error=None` zurück und unternimmt **sofort** einen neuen
   synchronen Zustellversuch (siehe Begründung unten).
5. **`NotificationOut`** um `attempts`/`next_retry_at` erweitert, `status`-Literal um `"failed_permanent"`.

## Begründung

- **Warum ein neuer, eigenständiger Poll-Loop statt eines retry-fähigen `create_and_send`, das selbst
  wartet/wiederholt**: `create_and_send` läuft synchron im NATS-Konsumenten-Handler bzw. im
  `POST /notifications`-Request-Response-Zyklus — ein Backoff-Warten dort würde entweder den NATS-
  Konsumenten blockieren (verzögert JEDE nachfolgende Nachricht auf demselben Durable-Konsumenten) oder
  den HTTP-Request unzumutbar lange offenhalten. Ein separater, asynchroner Poll-Loop (exakt wie im
  Roadmap-Plan vorgesehen: "wird ein neuer, eigener Retry-Poll-Loop ergänzt ... statt den NATS-Handler
  selbst zu blockieren") entkoppelt die Wiederholung vollständig vom ersten, weiterhin schnellen
  synchronen Versuch.
- **Warum `attempt_delivery` als eigene, öffentliche Funktion statt weiterhin inline in
  `create_and_send`**: sie wird jetzt von drei Stellen aufgerufen (erster Versuch, Poll-Loop-
  Wiederholung, manueller Retry) — dieselbe Erwägung wie bei `archival-service`s `mark_failed`.
- **Warum `status` bei einem retry-fähigen Fehlschlag `"failed"` bleibt statt eines neuen
  Zwischenwerts**: anders als bei `archival-service` (mehrere Phasen: `pending`/`locked`/`copied`/...)
  gibt es hier nur einen einzigen Zustellschritt — `"failed"` beschreibt bereits korrekt "dieser eine
  Schritt ist gerade nicht erfolgreich", ob retry-fähig oder nicht wird über `attempts`/`next_retry_at`
  ausgedrückt, nicht über eine weitere Statuskategorie. Bestehende Tests/Konsumenten, die auf
  `status == "failed"` nach einem EINZELNEN Fehlschlag prüfen, bleiben dadurch unverändert gültig (kein
  Breaking Change am Status-Vokabular für den bereits bekannten Fall).
- **Warum der manuelle Retry-Endpunkt SOFORT synchron zustellt statt nur auf `pending` zurückzusetzen
  und den nächsten Poll-Tick abzuwarten** (bewusster Unterschied zu `archival-service`s
  `reset_for_retry`): eine Notification ist ein einzelner, leichtgewichtiger Zustellschritt (eine
  SMTP-/HTTP-Anfrage), kein mehrphasiger Prozess mit mehreren Sekunden/Minuten Laufzeit — ein Admin, der
  auf "erneut versuchen" klickt, erwartet ein sofortiges Ergebnis in der Antwort, nicht ein Warten auf
  den nächsten Poll-Tick (bis zu 60s Default-Intervall).
- **Warum KEINE RBAC-Gate für den neuen Retry-Endpunkt**: `notification-service` hat aktuell überhaupt
  keine `permission-service`-Integration (kein `dms-permission-client`, keine RBAC-Prüfung an irgendeinem
  bestehenden Endpunkt außer der Empfänger-Existenzprüfung) — eine komplette RBAC-Neueinführung nur für
  diesen einen Endpunkt wäre Umfangsausweitung weit über "Retry/Backoff ergänzen" hinaus und war nicht
  Teil dieser Roadmap-Session (Phase 19 war explizit die RBAC-Phase). Bleibt bewusst wie alle anderen
  bestehenden `GET`-Endpunkte dieses Service ungegatet.
- **Warum `in_app` nie retry-fähig ist**: es gibt keinen echten Zustellschritt, der fehlschlagen könnte
  (reine DB-Persistenz) — `attempt_delivery`s `try`-Block deckt nur `email`/`webhook` mit einem
  `DeliveryError`-Pfad ab, `in_app` fällt durch zu `status="sent"`, exakt wie vor dieser Session.

## Konsequenzen

- **Migration bereits laufender Installationen**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` im
  Lifespan (gleiches Ad-hoc-Migrationsmuster wie `archival-service`, `document-service`) für beide neuen
  Spalten.
- **Tests**: `notification-service` 40 (vorher 30, +10: Repository-Ebene — Backoff-Verhalten unterhalb/
  bei Erschöpfung von `max_notification_attempts`, `list_due_for_retry`-Filterung nach Status UND
  Backoff-Fenster, `retry_now`; API-Ebene — neuer `/retry`-Endpunkt inkl. `404`/`409`/erfolgreichem
  Neustart; neue `test_main.py` — `_run_retry_tick` greift eine fällige Notification auf, überspringt
  eine noch nicht fällige).
- **Neue `session_factory`-Fixture in `conftest.py`** (fehlte bislang, anders als bei `archival-service`)
  — nötig für die neuen Poll-Tick-Tests.
- Kein neues Event (`notification.sent`/`.failed` reicht weiterhin — ein Wiederholungsversuch, der
  letztlich erfolgreich ist, publiziert `notification.sent` wie ein Erstversuch; ein `failed_permanent`-
  Übergang publiziert weiterhin `notification.failed`, keine neue, dritte Event-Variante nötig).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart, Migration bestätigt):
  ein Webhook an eine unerreichbare URL erreicht nach `max_notification_attempts` Versuchen
  `failed_permanent`; `POST /notifications/{id}/retry` liefert `409` für eine noch retry-fähige
  Notification und stellt bei einer `failed_permanent`-Notification sofort erneut zu (bleibt bei
  weiterhin unerreichbarem Ziel korrekt `failed_permanent`); der Poll-Loop greift eine künstlich
  fällig gesetzte Notification auf.
