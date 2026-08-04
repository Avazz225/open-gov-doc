# reporting-service

**Verantwortung:** Standardberichte (5.4a, seit P7-S2b) — vordefinierte, exportierbare, planbar per E-Mail versendbare Auswertungen über den Systemzustand: Dokumentenaufkommen, offene Workflow-Aufgaben, Speicherverbrauch je Backend, Nutzeraktivität.

**Konzept-Referenz:** 5.4/5.4a
**Eigenes Postgres-Schema:** `reporting` (Tabellen `document_created_event`, `report_schedule`, `report_run`)

## Architekturentscheidungen

- **Nur 4 von 5 Konzept-Beispielberichten** — Lizenzauslastung bewusst ausgeklammert, da es noch keinen License Service gibt (Konzept 3.2b/Phase 9, `P9-S0` steht noch aus). 5.4 listet die Berichte explizit als Beispiele ("z. B. ..."), keine abschließende Liste. Nachrüstbar, sobald Phase 9 den License Service liefert.
- **Gemischte Datenquelle je Bericht** (Konzept 3.1 erlaubt explizit beides: "über Events/Read-Modelle **oder** einen dedizierten Reporting-Service mit eigener replizierter Sicht"):
  - **Dokumentenaufkommen** = echtes Read-Modell (`document_created_event`, gespeist durch Konsum von `document.created`) — der einzige Bericht, bei dem eine eigene Zeitreihen-Aggregation wirklich neuen Wert schafft (nichts sonst hält diese Historie).
  - **Offene Workflow-Aufgaben**/**Nutzeraktivität** = synchrone Live-Abfragen bei Erstellung des Berichts gegen `workflow-service` (`GET /instances`+`.../tasks`) bzw. `audit-service` (`GET /events?actor=&since=&until=` — die in P7-S2 gebaute Filter-API, ihr erster echter Konsument). Kein Event markiert eine Aufgabe als "neu fällig" (nur Start/Abschluss werden publiziert) — ein Read-Modell würde hier inhärent veralten.
  - **Speicherverbrauch je Backend** = synchrone Live-Abfrage gegen `storage-service`s neuen `GET /storage/usage`-Endpunkt (seit P7-S2b, siehe `docs/services/storage-service.md`).
- **`document.created`-Payload-Erweiterung**: `document-service` sendet seit P7-S2b zusätzlich `folder_id` im Event-Payload — ohne dieses Feld könnte das Dokumentenaufkommen-Read-Modell nicht nach Ordner gruppieren, ohne bei jedem Event synchron beim Document Service nachzufragen (der Live-Join, den 3.1 vermeiden will).
- **Export ohne neue Bibliothek**: CSV (Python-Standardbibliothek) + PDF (`reportlab`, bereits echte Dependency in `rendering-service`, hier wiederverwendet).
- **Planbarer Versand ohne E-Mail-Anhang**: `notification-service`s `NotificationCreate` kennt nur `channel/recipient/subject/body`, kein Attachment-Feld — das nachzurüsten wäre ein eigener Schnitt an einem fremden Service. Stattdessen erzeugt ein `_report_schedule_poll_loop` (exaktes Poll-Idiom wie `document-service`s `_retention_poll_loop`/`workflow-service`s `_sla_poll_loop`) bei Fälligkeit den Bericht, lädt ihn über `storage-service` unter `reports/{schedule_id}/{run_id}.{format}` hoch (`ReportRun`-Zeile), und ruft `notification-service` mit einer Text-E-Mail auf, die einen Downloadlink auf `GET /report-runs/{id}/download` enthält (Proxy-Zugriff, gleiches Muster wie Dokument-Downloads). Ad-hoc-Exporte (`.../export`) werden **nicht** persistiert — nur geplante/versendete Läufe brauchen einen Ablageort, auf den der E-Mail-Link zeigen kann.
- **`gateway_base_url` getrennt von `self_address`**: `self_address` (Registry-Selbstregistrierung) ist ein interner Docker-DNS-Name, von außerhalb des Docker-Netzwerks (z. B. einem echten E-Mail-Client) nicht erreichbar. Der Downloadlink in der Planungs-E-Mail nutzt daher eine eigene, extern erreichbare `Settings.gateway_base_url`.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/reports/document-volume?since=&until=&folder_id=&group_by=day\|week\|month` | Dokumentenaufkommen aus dem eigenen Read-Modell, gruppiert nach Zeitraum (+optional Ordner) |
| `GET` | `/reports/document-volume/export?format=csv\|pdf&...` | Gleiche Filter, CSV/PDF-Download |
| `GET` | `/reports/open-workflow-tasks` | Live-Abfrage offener Workflow-Aufgaben gegen `workflow-service` |
| `GET` | `/reports/open-workflow-tasks/export?format=csv\|pdf` | CSV/PDF-Download |
| `GET` | `/reports/storage-usage` | Live-Abfrage `GET /storage/usage` gegen `storage-service` |
| `GET` | `/reports/storage-usage/export?format=csv\|pdf` | CSV/PDF-Download |
| `GET` | `/reports/user-activity?actor=&since=&until=` | Live-Abfrage gegen `audit-service` (`GET /events`-Filter-API), clientseitig nach `(actor, event_type)` aggregiert |
| `GET` | `/reports/user-activity/export?format=csv\|pdf&...` | CSV/PDF-Download |
| `POST` | `/report-schedules` | Planung anlegen (`report_type`, `format`, `frequency: "daily"\|"weekly"\|"monthly"`, `recipient_email`, optional `filters`) |
| `GET` | `/report-schedules` | Alle Planungen |
| `DELETE` | `/report-schedules/{id}` | Planung entfernen |
| `GET` | `/report-runs/{id}/download` | Proxy-Download eines erzeugten Berichtslaufs (aus `storage-service`) — Ziel des Downloadlinks in der Planungs-E-Mail |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `document_created_event`: `id`, `document_id`, `folder_id` (nullable), `occurred_at` — insert-only Read-Modell, eine Zeile je konsumiertem `document.created`.
- `report_schedule`: `id`, `report_type`, `format`, `frequency` (`daily`/`weekly`/`monthly`), `recipient_email`, `filters` (JSON), `next_run_at`, `last_run_at`, `created_at`.
- `report_run`: `id`, `schedule_id` (FK), `report_type`, `format`, `storage_object_key`, `content_type`, `generated_at` — eine Zeile je tatsächlich versendetem geplanten Lauf (nicht für Ad-hoc-Exporte).

`advance_next_run` schreibt `next_run_at` nach jedem Lauf fort: `daily`/`weekly` über einfache `timedelta`, `monthly` über `calendar.monthrange` (behandelt Tagesüberlauf korrekt, z. B. 31. Januar → 28./29. Februar).

## Poll-Loop (Planbarer Versand)

`_report_schedule_poll_loop` (Lifespan-Hintergrundtask, `Settings.report_poll_interval_seconds`, Default 3600s) prüft periodisch `list_due_schedules` (`next_run_at <= now`). Für jede fällige Planung: Bericht erzeugen (`_generate_report`, gleicher Renderpfad wie die Export-Endpunkte), unter `reports/{schedule_id}/{run_id}.{format}` bei `storage-service` hochladen, `ReportRun`-Zeile anlegen, `next_run_at` fortschreiben, E-Mail mit Downloadlink über `notification-service` verschicken. Die eigentliche Tick-Logik ist als eigenständige, direkt aufrufbare `_run_due_schedules(session_factory)` extrahiert — testbar ohne die Endlosschleife selbst laufen zu lassen.

## Events

**Konsumiert** (`document.>`, nur `document.created` relevant, alle anderen `document.*`-Events werden ignoriert — gleiches Dispatch-Muster wie `rendering-service`): schreibt eine Zeile in `document_created_event`.

Kein eigener publizierter Event-Stream — dieser Service ist ein reiner Aggregator/Auswerter, keine Quelle für Geschäftsereignisse. Berichts-Erzeugung/-Versand selbst wird **nicht** als Event publiziert (kein Konsument dafür vorgesehen).

**NATS-JetStream-Backfill**: die neue `document.>`-Subscription lieferte beim ersten Start rückwirkend die gesamte historische `document.created`-Ereignishistorie aus (gleiches Phänomen wie bei `folder.>` in P7-S2) — das Read-Modell war dadurch sofort mit echten historischen Daten gefüllt, nicht erst ab dem Rollout-Zeitpunkt.

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `reporting.>` — aktuell ohne Wirkung, da dieser Service keine eigenen Events publiziert (Vorbereitung für einen künftigen Bedarf, gleiches Muster wie bei jedem neuen Service-Stream).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Tests

- `uv run pytest services/reporting-service/tests`: Repository (Aggregation nach Tag/Woche/Monat, Ordnerfilter, `advance_next_run` inkl. Monatsüberlauf/Jahreswechsel, Schedule-CRUD, `list_due_schedules`), Consumer (`document.created` legt Zeile an inkl./exkl. `folder_id`, andere `document.*`-Events ignoriert), Reports (alle vier Aggregationsfunktionen gegen Fake-Clients, `to_csv`/`to_pdf` inkl. PDF-Magic-Bytes-Check), API (alle Endpunkte inkl. Export-Content-Type, Schedule-CRUD, Download-Proxy, Poll-Tick inkl. E-Mail-Versand über einen dedizierten `poll_env`-Fixture, das `TestClient`/Lifespan bewusst umgeht — direkte `await`-DB-Zugriffe auf `app.state.session_factory` aus einer pytest-asyncio-Korutine schlagen sonst mit `RuntimeError: ... attached to a different loop` fehl, da `TestClient` seine eigene Event-Loop nutzt). **37 Tests, alle grün.**
- **Live-Docker-Verifikation** (P7-S2b): Container gebaut+gestartet, `/healthz`, `/reports/document-volume` (zeigte echte, per Backfill rückwirkend eingespielte historische Daten inkl. korrektem `folder_id`), `/reports/storage-usage` (reale Backend-Größen), `/reports/open-workflow-tasks` (korrekt leer ohne offene Instanzen), CSV-/PDF-Export (`text/csv`/`application/pdf`, PDF beginnt mit `%PDF`) gegen den echten laufenden Stack geprüft. Zusätzlich der vollständige Planungszyklus mit auf 15s abgesenktem `DMS_REPORT_POLL_INTERVAL_SECONDS` durchgespielt: Planung → Poll-Tick → Storage-Upload → E-Mail über Mailpit → Downloadlink-Proxy-Download → `next_run_at` korrekt fortgeschrieben (siehe `PROGRESS.md` "P7-S2b" für die dabei gefundenen zwei Randfälle: die `audit-service`-Sortierreihenfolge-Korrektur und `notification-service`s bestehende Empfänger-Validierung).

## Offene Punkte

- **Lizenzauslastung nicht gebaut** (s. o.) — nachzurüsten, sobald Phase 9 den License Service liefert.
- **Kein Rollen-/Berechtigungscheck** auf den Berichts-Endpunkten — wie bei den meisten administrativen Endpunkten dieses Systems bislang ungated (siehe `PROGRESS.md` "Autorisierung"), nur über die Admin-UI-Navigation praktisch auf Admins beschränkt.
- **Ad-hoc-Exporte werden nicht persistiert** (bewusst, s. o.) — ein Nutzer, der einen Export-Link teilen möchte, muss die Datei manuell weiterreichen; nur geplante Läufe haben einen dauerhaften Downloadlink.
- **Keine Forensik-Trace-Integration** — 5.4b ("alle Aktionen von Nutzer X") ist als eigene Session **P7-S2c** vorgesehen und baut direkt auf der hier erstmals genutzten Audit-Filter-API auf, ist aber kein Bestandteil dieses Service.
- **`recipient_email` einer Planung muss ein echtes `auth-service`-Konto sein** — `notification-service` lehnt jede E-Mail an eine unbekannte Adresse mit `400` ab (bestehende Empfänger-Validierung seit P6-S6, siehe `docs/services/notification-service.md`). Dieser Service selbst validiert das beim Anlegen einer Planung **nicht** vorab — ein Tippfehler oder eine beliebige externe Adresse fällt erst beim nächsten fälligen Poll-Tick auf (Fehler wird geloggt, Planung bleibt bestehen und wird beim nächsten Tick erneut versucht, kein Statusfeld an der Planung selbst zeigt den Fehlschlag an). Weder im Backend noch in der Admin-UI kommuniziert — ein sinnvoller künftiger Schnitt wäre eine serverseitige Vorabprüfung gegen `auth-service` bei `POST /report-schedules`.
