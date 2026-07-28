# storage-service

**Verantwortung:** Storage-Abstraktionsschicht über austauschbare Backend-Plugins — Dateiinhalte liegen in einem oder mehreren konfigurierten Backends (Redundanz, seit P3-S4; beliebig viele, auch gleichartige Instanzen, seit P5b-S6), die Shared DB hält nur Metadaten (Referenz, Prüfsumme, Größe, Kopien-Status, Datenträger-Identität) (Konzept 3.6).

**Konzept-Referenz:** 3.6
**Eigenes Postgres-Schema:** `storage` (Tabellen `object_metadata`, `object_copy`, `backend_identity`, `guard_config`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `PUT` | `/objects/{key:path}` | Hochladen, berechnet SHA-256, schreibt gemäß Schreibstrategie auf die konfigurierten Ziele, upserted Metadaten |
| `GET` | `/objects/{key:path}` | Herunterladen - liest von der ersten Kopie mit Status `ok` in Zielpriorität, automatischer Fallback (404, wenn keine Kopie verfügbar) |
| `DELETE` | `/objects/{key:path}` | Löschen auf allen Zielen (idempotent), dann Metadaten + Kopien-Einträge |
| `GET` | `/object-metadata/{key:path}` | Metadaten lesen |
| `GET` | `/objects/{key:path}/copies` | Kopien-Status je konfiguriertem Ziel (`pending`/`ok`/`failed`/`failed_permanent`) |
| `GET` | `/object-verify/{key:path}` | Fixity-Check des Primärziels: Prüfsumme neu lesen, mit Referenzwert vergleichen |
| `GET` | `/object-verify/{key:path}/all` | Fixity-Check über **alle** konfigurierten Ziele, aktualisiert `object_copy` |
| `POST` | `/replication/process-pending` | Retry-Queue verarbeiten - repliziert ausstehende Kopien nach, für periodischen externen Aufruf gedacht |
| `GET` | `/guard-config` | Aktuelle Wächter-Konfiguration (`allow_degraded_start`) — legt beim ersten Aufruf die Default-Zeile an (P5b-S6) |
| `PUT` | `/guard-config` | Aktualisiert `allow_degraded_start` — wirkt erst beim **nächsten** Start, nicht auf die laufende Instanz |
| `GET` | `/guard-status` | Je konfiguriertem Ziel: zuletzt bestätigte Geräte-ID, Zeitpunkt, Anzahl noch nicht replizierter Kopien (Admin-UI-Statusblock) |
| `POST` | `/guard-status/{target_id}/reidentify` | Akzeptiert einen beabsichtigten Datenträger-Wechsel zur Laufzeit (kein Neustart nötig), P5c-S2 |
| `GET` | `/healthz` | Health-Check inkl. aktiven Zielen und Schreibstrategie |

## Backend-Plugin-Interface (3.6)

`StorageBackend` (ABC): `write`, `read`, `delete`, `exists`, `checksum`. Zwei Implementierungen in dieser Session:

- **`LocalFilesystemBackend`** — deckt sowohl "lokales Dateisystem" als auch **NFS** ab: In Kubernetes ist der konfigurierte Pfad der Mountpunkt eines PVC, das Backend sieht nur einen Ordner, unabhängig davon, ob NFS oder Block-Storage dahintersteht. Schreibt atomar (temporäre Datei + `os.replace`) statt mit plattformspezifischem `fcntl`-Locking — dessen Semantik ist über NFS-Implementierungen hinweg inkonsistent, während atomares Rename-nach-Schreiben auf NFSv4+ zuverlässig funktioniert und Teilschreib-Korruption bei gleichzeitigen Schreibern auf denselben Key verhindert. Nebenläufige *Bearbeitung* eines Dokuments ist Aufgabe des Document-Service-Lockings (4.2), nicht dieser Schicht.
- **`S3Backend`** — `aioboto3`, Werkseinstellung MinIO, funktioniert identisch gegen jeden S3-kompatiblen Provider.

Beide sind unabhängig getestet: `LocalFilesystemBackend` gegen echtes Dateisystem (`tmp_path`), `S3Backend` gegen echtes MinIO (nicht gemockt).

## Datenmodell

- `object_metadata`: `object_key` (PK), `backend` (Ziel-`id` des Primärziels zum Zeitpunkt der Anlage/letzten Überschreibung — seit P5b-S6 eine Ziel-`id`, kein Backend-*Typ* mehr, siehe unten), `checksum_sha256`, `size_bytes`, `content_type`, `created_at`, `updated_at`.
- `object_copy`: `object_key` + `backend_id` (zusammengesetzter PK, FK auf `object_metadata`), `status` (`pending`/`ok`/`failed`/`failed_permanent`), `checksum_sha256`, `attempts`, `last_error`, `created_at`, `updated_at` — eine Zeile je konfiguriertem Ziel und Objekt.
- `backend_identity` (neu, P5b-S6): `target_id` (PK), `device_id`, `verified_at` — zuletzt bestätigte Geräte-ID je konfiguriertem Ziel, unabhängig vom Ziel selbst gespeichert (siehe "Datenträger-Wechsel-Wächter" unten).
- `guard_config` (neu, P5b-S6): einzelne Zeile mit fester `id=1`, `allow_degraded_start`, `updated_at` — gleiches Muster wie `ocr_config` (ocr-service, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md)).

## Ziel-Set: beliebig viele Backend-Instanzen (3.6, seit P5b-S6)

`Settings.targets` ist eine echte Liste von `BackendTargetConfig`-Einträgen (`id`, `type: "local"|"s3"`, plus typspezifische Zugangsdaten) statt der vorherigen festen Zwei-Slot-Struktur (`backend`/`secondary_backend`, [ADR 0004](../adr/0004-storage-redundancy-scope.md)) — **`id`, nicht `type`, ist der eindeutige Schlüssel**, wodurch beliebig viele gleichartige Instanzen im selben Ziel-Set möglich sind (z. B. zwei unabhängige S3-Provider zusätzlich zu einem lokalen/NFS-Ziel). Konfiguriert als JSON-Liste in `DMS_TARGETS` (pydantic-settings dekodiert komplexe Feldtypen nativ aus einer einzelnen Umgebungsvariable, siehe [ADR 0017](../adr/0017-storage-device-identity-guard.md)):

```
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"s3-eu","type":"s3","endpoint_url":"...","access_key":"...","secret_key":"...","bucket":"...","region":"..."}]'
```

Primärziel ist immer der erste Eintrag (bestimmt Schreib-Synchronität bei `primary_async` sowie die Lesepriorität). `replication.py` selbst war laut ADR 0004 bereits vollständig generisch gegenüber beliebigen Ziel-`id`-Strings — die frühere Zwei-Slot-Beschränkung saß ausschließlich in `Settings`/`backends/__init__.py`, dort ist sie mit dieser Session aufgelöst.

**Rebalancing bei neu hinzugefügtem Ziel** (seit **P5c-S2**): eine Ziel-Set-Änderung erfordert ohnehin einen Neustart (`Settings` wird nur beim Start gelesen) — genau dieser Neustart löst über den Erststart-Bootstrap des Datenträger-Wechsel-Wächters (siehe unten) automatisch auch das Rebalancing aus: `repository.seed_pending_copies_for_new_target` legt für jedes bereits existierende Objekt, das noch keine Kopie auf dem neuen Ziel hat, eine `pending`-Zeile an, die dieselbe Retry-Queue (`POST /replication/process-pending`) danach nachzieht. Kein separater Trigger-Mechanismus, keine Admin-UI-Aktion nötig.

## Redundanz & Fixity (Konzept 3.6, seit P3-S4)

- **Schreibstrategien** (`Settings.write_strategy`): `quorum` (synchron, Erfolg erst ab `quorum_count` bestätigten Zielen, bei Nichterreichen werden bereits erfolgreiche Teilkopien best-effort zurückgerollt) oder `primary_async` (Werkseinstellung für den allgemeinen Betrieb: nur das Primärziel synchron, weitere Ziele bleiben `pending` und werden über `POST /replication/process-pending` nachgezogen — Retry-Queue mit `max_replication_attempts`, danach `failed_permanent` + Error-Log als Alarmierungs-Ersatz).
- **Lese-Fallback**: `GET /objects/{key}` liest von der ersten Kopie mit Status `ok` in Zielpriorität (Primärziel zuerst).
- **Fixity-Check je Kopie**: `GET /object-verify/{key}/all` liest die Prüfsumme aus jedem Backend neu und vergleicht sie mit dem in `object_metadata` hinterlegten Referenzwert — erkennt Bit-Rot/Manipulation je Ziel unabhängig, aktualisiert `object_copy.status`.
- Die orchestrierende Logik (`replication.py`) ist backend-agnostisch (arbeitet mit `dict[str, StorageBackend]` + `list[str]` Zielpriorität) und unabhängig von der FastAPI-App-Singleton-Konfiguration testbar.

## Datenträger-Wechsel-Wächter (3.6, seit P5b-S6, [ADR 0017](../adr/0017-storage-device-identity-guard.md))

Schützt gegen einen versehentlich getauschten/zurückgesetzten Datenträger, der sonst stillschweigend als "leeres, aber gültiges" Ziel akzeptiert würde:

- Jedes Ziel bekommt bei erstmaliger Nutzung eine generierte Geräte-ID, abgelegt als Marker-Objekt unter dem reservierten Key `__dms_storage_identity__` — über das bestehende `StorageBackend.write`/`read`-Interface, keine neue Backend-Methode. Der reservierte Key enthält bewusst keinen Schrägstrich und kollidiert damit nicht mit echten, stets segmentierten Objekt-Keys (`typ/id/...`).
- Der **Referenzwert** liegt zusätzlich (nicht nur im Backend selbst) in `backend_identity` — bei einem tatsächlichen Rückfall auf ein leeres/falsches Medium fehlt die Marker-Datei im Backend gerade, ein reiner Selbstvergleich des Backends wäre daher wirkungslos.
- **Werkseinstellung: Startverweigerung** (`RuntimeError` vor `yield`, gleiches Fail-fast-Muster wie die bestehende `_validate_settings`-Prüfung), sobald ein Ziel nicht mit seinem bekannten Referenzwert übereinstimmt oder nicht erreichbar ist. Ein neu zum Ziel-Set hinzugefügtes Ziel (kein bekannter Referenzwert vorhanden) wird stattdessen automatisch "geprägt" — kein Fehlschlag beim Erststart.
- **Admin-Override** (`GuardConfig.allow_degraded_start`, `GET`/`PUT /guard-config`) erlaubt einen degradierten Start, **sofern mindestens ein Ziel nachweislich unverändert ist** — bewusst eine proaktiv gesetzte Standing-Policy statt eines Notfall-Schalters im Moment der Verweigerung (der Service, der die Freigabe entgegennehmen müsste, liefe ja gerade nicht; Postgres selbst ist von einem defekten Storage-Backend unabhängig, siehe ADR 0017).
- Im degradierten Fall werden alle `object_copy`-Zeilen der betroffenen Ziele automatisch auf `pending` zurückgesetzt (`repository.reset_copies_for_backend`) — die bereits bestehende Retry-Queue (`POST /replication/process-pending`) zieht sie nach, kein neuer Hintergrundtask (ADR 0004 gilt unverändert weiter).
- `GET /guard-status` zeigt je Ziel die zuletzt bestätigte Geräte-ID/Zeitpunkt sowie die Anzahl noch offener Kopien — ein Ziel mit `pending_copies > 0` befindet sich noch in der Wiederherstellung.
- **Korrekturmechanismus für beabsichtigte Datenträger-Wechsel** (seit **P5c-S2**): `POST /guard-status/{target_id}/reidentify` übernimmt eine bereits vorhandene Marker-Datei des neuen Geräts oder prägt (wie beim Erststart-Bootstrap) eine neue, aktualisiert `backend_identity` und setzt alle bisherigen Kopien des Ziels über `reset_copies_for_backend` auf `pending` zurück — funktional dieselbe Wiederherstellung wie beim automatischen degradierten Start, aber explizit vom Admin angestoßen und **ohne Neustart** (Admin-UI: Button "Datenträger-Wechsel akzeptieren" je Zeile in `/storage-guard/`). Ersetzt die zuvor nötige direkte Korrektur in der `backend_identity`-Tabelle.

## Events

Keine — Storage Service publiziert/konsumiert weiterhin keine Events.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery. Die Selbst-Registrierung passiert **nach** dem Datenträger-Wechsel-Wächter — ein verweigerter Start meldet den Service also gar nicht erst an (kein "healthy=false"-Registry-Eintrag, sondern schlicht kein Eintrag).

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/storage-service/tests` (**86 Tests**, davon 10 neu seit P5c-S2): Backend-Plugins, Fabrikfunktionen, Replikation unverändert. Neu: `repository.seed_pending_copies_for_new_target` (bereits abgedeckte Objekte bleiben unberührt, keine bestehenden Objekte → No-Op), `identity_guard.check_target_identity` seedet beim Erststart-Bootstrap automatisch Rebalancing-Kopien, `identity_guard.reidentify_target` (übernimmt eine vorhandene Marker-Datei des neuen Geräts, prägt sonst eine neue, setzt bestehende Kopien zurück), API-Tests für `POST /guard-status/{target_id}/reidentify` (unbekanntes Ziel → 404, Geräte-ID-Wechsel + Kopien-Reset).
- **Live-Verifikation ohne Mocking**: ein echter Datenträger-Wechsel wurde gegen den laufenden Container simuliert (Identitätsdatei manuell verändert, Neustart erzwungen) — Startverweigerung, Admin-Override + degradierter Start, automatische Nachreplikations-Vormerkung und `POST /replication/process-pending` alle 1:1 wie vorgesehen; siehe `PROGRESS.md` für den Ablauf. **Seit P5c-S2 zusätzlich verifiziert**: ein zur Laufzeit hinzugefügtes zweites Ziel bekam beim Neustart automatisch `pending`-Kopien für ein zuvor hochgeladenes Objekt (Rebalancing), und `POST /guard-status/{target_id}/reidentify` hat einen simulierten Datenträger-Wechsel ohne Neustart korrigiert.

## Offene Punkte

- **Konfiguration je Objekttyp/Ordner statt service-weit** — das Konzept erlaubt Overrides der Schreibstrategie pro Objekttyp/Ordner; dafür fehlt aktuell die Verbindung zwischen Object-Type/Folder Service und Storage Service.
- **Keine automatische periodische Ausführung** von `/object-verify/.../all` oder `/replication/process-pending` — beide sind bewusst reine On-Demand-Endpunkte, gedacht für einen externen Scheduler (siehe ADR 0004), noch nicht Teil dieser Session. Nach einem degradierten Start oder einem `reidentify`-Aufruf bleibt die Wiederherstellung deshalb so lange "hängen" (`pending_copies > 0`), bis jemand/etwas den Endpunkt aufruft.
- **Kein Entfernen eines Ziels aus dem Ziel-Set** — ein einmal konfiguriertes Ziel lässt sich aktuell nicht sauber "stilllegen" (die zugehörigen `object_copy`-Zeilen blieben verwaist); nur das *Hinzufügen* wurde mit P5c-S2 adressiert.
- Azure-Blob-Backend (Konzept 1a erwähnt es, nicht Teil dieser Session).
