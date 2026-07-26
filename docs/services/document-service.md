# document-service

**Verantwortung:** Dokumente als Kernentität (Konzept 2.1) — CRUD, dauerhafte Versionierung (2.1a, kein Überschreiben/Verwerfen), Bearbeitungssperre bei externer Bearbeitung inkl. Force-Unlock und Konfliktkopie (4.2). Hält selbst nie Dateiinhalte — jeder Byte-Zugriff läuft über die HTTP-API des Storage Service (3.6).

**Konzept-Referenz:** 2.1/2.1a/4.2
**Eigenes Postgres-Schema:** `document` (Tabellen `document`, `document_version`, `document_lock`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/documents` | Anlegen (multipart: `file`, `title`, `created_by`, optional `folder_id`/`object_type_id`/`attributes` als JSON-String) — erzeugt Dokument + Version 1 |
| `GET` | `/documents?folder_id=...` | Nicht gelöschte Dokumente eines Ordners (seit P4-S2, Grundlage der User-UI-Navigation) — unbekannter `folder_id` liefert `[]`, kein 404 |
| `GET` | `/documents/{id}` | Metadaten |
| `DELETE` | `/documents/{id}?deleted_by=...` | Weiche Löschung (`deleted_at` gesetzt, Metadaten bleiben) |
| `GET` | `/documents/{id}/content` | Inhalt der aktuellen Hauptversion |
| `GET` | `/documents/{id}/versions` | Alle Versionen inkl. Konfliktkopien (2.1a: nichts wird je verworfen) |
| `GET` | `/documents/{id}/versions/{n}` | Metadaten einer konkreten Version |
| `GET` | `/documents/{id}/versions/{n}/content` | Inhalt einer konkreten Version |
| `POST` | `/documents/{id}/versions` | Check-in (multipart: `file`, `expected_base_version_number`, `created_by`, optional `comment`) — siehe Konflikterkennung unten |
| `GET` | `/documents/{id}/lock` | Aktuelle Sperre oder `null` |
| `POST` | `/documents/{id}/lock` | Sperre setzen (`locked_by`, `session_id`, optional `timeout_seconds`) — 409 bei Fremdsperre |
| `DELETE` | `/documents/{id}/lock` | Regulärer Unlock (`released_by`) — 403, wenn nicht der Halter |
| `POST` | `/documents/{id}/lock/force-release` | Administrativer Force-Unlock (`released_by`, optional `reason`) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `document`: `id`, `title`, `folder_id`/`object_type_id` (opake Referenzen, s. u.), `attributes` (JSON, Custom-Felder gemäß Objekttyp), `current_version_number` (Zeiger auf die Hauptversion), `deleted_at`, `created_by/at/updated_at`.
- `document_version`: `document_id`, `version_number`, `storage_object_key`, `filename`, `content_type`, `size_bytes`, `checksum_sha256`, `is_conflict`, `based_on_version_number`, `comment`, `created_by/at`. Jede Zeile bleibt für immer abrufbar (2.1a).
- `document_lock`: genau eine aktive Zeile je gesperrtem Dokument (`document_id` als PK) — `locked_by`, `session_id`, `based_on_version_number`, `locked_at`, `expires_at`.

`folder_id`/`object_type_id` sind opake Referenzen ohne FK-Erzwingung über Service-Grenzen hinweg, werden aber seit P3-S3 aktiv geprüft: `folder_id` (falls gesetzt) muss beim Folder Service existieren (sonst 400), `object_type_id` (falls gesetzt) validiert `attributes`+`title` gegen den Object-Type Service (sonst 400 mit Fehlerliste). `attributes` wird nur bei der Erstellung gesetzt/validiert — es gibt noch keinen Endpunkt, um sie nachträglich zu ändern (offener Punkt).

**Ad-hoc-Schema-Migration**: `attributes` kam erst in P3-S3 zur bestehenden `document`-Tabelle dazu. Ohne Alembic (siehe `CONTRIBUTING.md`) übernimmt ein `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in der Lifespan-Startup-Routine diese additive, defaultbehaftete Änderung idempotent — funktioniert nur für genau diese Art von Änderung (neue, nullable/defaultbehaftete Spalte), nicht für Umbenennungen/Typänderungen/Entfernen von Spalten. Sobald Schemaänderungen komplexer werden, wird echtes Alembic-Tooling nötig (siehe „Offene Entscheidungen" in `PROGRESS.md`).

## Speicherung der Inhalte (3.6-Anbindung)

Objektschlüssel sind **inhaltsadressiert**: `documents/{document_id}/{sha256}`. Das vermeidet die Henne-Ei-Reihenfolge "Upload braucht die noch nicht vergebene Versionsnummer" und dedupliziert identische Inhalte innerhalb desselben Dokuments automatisch (z. B. wiederholtes Hochladen derselben Datei). Document Service spricht dafür ausschließlich `PUT`/`GET /objects/{key}` des Storage Service über HTTP an — kein Zugriff auf dessen Interna oder direkte Backend-Nutzung.

## Bearbeitungssperre & Konfliktbehandlung (4.2)

- Eine Sperre ist an `locked_by` + `session_id` gebunden und läuft nach einem konfigurierbaren Timeout automatisch ab (`default_lock_timeout_seconds`, keine Hintergrundprüfung nötig — Ablauf wird beim nächsten Zugriff bewertet, analog zum Registry-Service-Muster).
- **Force-Unlock löscht die Sperre vollständig** statt sie in einen dritten "überwacht"-Zustand zu versetzen (Abweichung von der wörtlichen Konzeptbeschreibung, siehe **ADR 0002** für die Begründung). Der Schutz vor stillem Datenverlust entsteht stattdessen durch eine **immer aktive optimistische Konflikterkennung** beim Check-in:
  - Jeder Check-in gibt `expected_base_version_number` an.
  - Stimmt dieser Wert mit der aktuellen Hauptversion überein → regulärer Check-in, neue Hauptversion.
  - Weicht er ab (z. B. weil in der Zwischenzeit jemand anderes nach einem Force-Unlock eingecheckt hat) → **Konfliktkopie**: eigenständige, weiterhin abrufbare Version (`is_conflict=true`, Dateiname `<name>_conflict_<user>_<zeitstempel>`), der Hauptversions-Zeiger bewegt sich nicht.
- Ein eigener Check-in beendet immer die eigene Sperre (auch im Konfliktfall — die Ausgangsbasis war ohnehin veraltet).
- Vier-Augen-Prinzip (4.3) für Force-Unlock ist noch nicht verdrahtet, folgt mit dem generischen Approval-Mechanismus in P6-S4.

## Events

**Publiziert** (Stream `document`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `document.created` | `{title, created_by}` |
| `document.version.created` | `{version_number, is_conflict, created_by}` |
| `document.lock.force_released` | `{original_locked_by, released_by, reason}` |
| `document.deleted` | `{deleted_by}` |

**Konsumiert:** keine.

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `document.>` (vorher nur `registry.>`) — 4.2 verlangt explizit vollständige Auditierung von Force-Unlock/Konfliktkopie. Force-Unlock und die daraus ggf. entstehende Konfliktkopie erzeugen zwei separate, aber im Audit-Trail über `subject=document_id` verknüpfbare Ereignisse.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Kein Vier-Augen-Prinzip für Force-Unlock (4.3, folgt P6-S4).
- Aufbewahrung/Zwangslöschung/Löschregister (5.2/5.2a) nicht Teil dieser Session — `DELETE` ist eine einfache weiche Löschung, keine Compliance-Funktion (folgt Phase 7).
- Kein Endpunkt, um `attributes`/`title` eines bestehenden Dokuments nachträglich zu ändern (und damit erneut zu validieren) — nur bei Erstellung.
- Umlaufmappen-Referenzen (2.3) und Ersatzdarstellungen (2.4) sind eigene, spätere Sessions (P6-S3 bzw. P5-S2) und greifen auf Dokumente/Versionen dieses Service zu, ohne dass hier bereits etwas vorbereitet wurde.
