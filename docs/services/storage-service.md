# storage-service

**Verantwortung:** Storage-Abstraktionsschicht über austauschbare Backend-Plugins — Dateiinhalte liegen in einem oder mehreren konfigurierten Backends (Redundanz, seit P3-S4), die Shared DB hält nur Metadaten (Referenz, Prüfsumme, Größe, Kopien-Status) (Konzept 3.6).

**Konzept-Referenz:** 3.6
**Eigenes Postgres-Schema:** `storage` (Tabellen `object_metadata`, `object_copy`)

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
| `POST` | `/replication/process-pending` | Retry-Queue verarbeiten - repliziert ausstehende Sekundärkopien nach, für periodischen externen Aufruf gedacht |
| `GET` | `/healthz` | Health-Check inkl. aktiven Zielen und Schreibstrategie |

## Backend-Plugin-Interface (3.6)

`StorageBackend` (ABC): `write`, `read`, `delete`, `exists`, `checksum`. Zwei Implementierungen in dieser Session:

- **`LocalFilesystemBackend`** — deckt sowohl "lokales Dateisystem" als auch **NFS** ab: In Kubernetes ist der konfigurierte Pfad der Mountpunkt eines PVC, das Backend sieht nur einen Ordner, unabhängig davon, ob NFS oder Block-Storage dahintersteht. Schreibt atomar (temporäre Datei + `os.replace`) statt mit plattformspezifischem `fcntl`-Locking — dessen Semantik ist über NFS-Implementierungen hinweg inkonsistent, während atomares Rename-nach-Schreiben auf NFSv4+ zuverlässig funktioniert und Teilschreib-Korruption bei gleichzeitigen Schreibern auf denselben Key verhindert. Nebenläufige *Bearbeitung* eines Dokuments ist Aufgabe des Document-Service-Lockings (4.2), nicht dieser Schicht.
- **`S3Backend`** — `aioboto3`, Werkseinstellung MinIO, funktioniert identisch gegen jeden S3-kompatiblen Provider.

Beide sind unabhängig getestet: `LocalFilesystemBackend` gegen echtes Dateisystem (`tmp_path`), `S3Backend` gegen echtes MinIO (nicht gemockt).

## Datenmodell

- `object_metadata`: `object_key` (PK), `backend` (Primärziel), `checksum_sha256`, `size_bytes`, `content_type`, `created_at`, `updated_at`.
- `object_copy`: `object_key` + `backend_id` (zusammengesetzter PK, FK auf `object_metadata`), `status` (`pending`/`ok`/`failed`/`failed_permanent`), `checksum_sha256`, `attempts`, `last_error`, `created_at`, `updated_at` — eine Zeile je konfiguriertem Ziel und Objekt.

## Redundanz & Fixity (Konzept 3.6, seit P3-S4)

- **Zwei konfigurierbare Ziele** (Primär- und optionales Sekundärziel, je `local`/`s3`) statt einer generischen Ziel-Menge — Begründung: **ADR 0004**.
- **Schreibstrategien** (`Settings.write_strategy`): `quorum` (synchron, Erfolg erst ab `quorum_count` bestätigten Zielen, bei Nichterreichen werden bereits erfolgreiche Teilkopien best-effort zurückgerollt) oder `primary_async` (Werkseinstellung für den allgemeinen Betrieb: nur das Primärziel synchron, Sekundärziel bleibt `pending` und wird über `POST /replication/process-pending` nachgezogen — Retry-Queue mit `max_replication_attempts`, danach `failed_permanent` + Error-Log als Alarmierungs-Ersatz).
- **Lese-Fallback**: `GET /objects/{key}` liest von der ersten Kopie mit Status `ok` in Zielpriorität (Primärziel zuerst).
- **Fixity-Check je Kopie**: `GET /object-verify/{key}/all` liest die Prüfsumme aus jedem Backend neu und vergleicht sie mit dem in `object_metadata` hinterlegten Referenzwert — erkennt Bit-Rot/Manipulation je Ziel unabhängig, aktualisiert `object_copy.status`.
- Die orchestrierende Logik (`replication.py`) ist backend-agnostisch (arbeitet mit `dict[str, StorageBackend]` + `list[str]` Zielpriorität) und unabhängig von der FastAPI-App-Singleton-Konfiguration testbar.

## Events

Keine — Storage Service publiziert/konsumiert weiterhin keine Events.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- **Mehr als zwei Ziele / mehrere Instanzen desselben Backend-Typs** (z. B. zwei S3-Provider gleichzeitig) — braucht eine Settings-Struktur mit einer echten Ziel-Liste statt der aktuellen zwei festen Felder (`backend`/`secondary_backend`), siehe ADR 0004.
- **Rebalancing** beim Hinzufügen/Entfernen eines Backends aus einem Ziel-Set — konzeptionell mit dem Migrationsprozess aus 7.2 verwandt, eigene spätere Session.
- **Konfiguration je Objekttyp/Ordner statt service-weit** — das Konzept erlaubt Overrides der Schreibstrategie pro Objekttyp/Ordner; dafür fehlt aktuell die Verbindung zwischen Object-Type/Folder Service und Storage Service.
- **Keine automatische periodische Ausführung** von `/object-verify/.../all` oder `/replication/process-pending` — beide sind bewusst reine On-Demand-Endpunkte, gedacht für einen externen Scheduler (siehe ADR 0004), noch nicht Teil dieser Session.
- Azure-Blob-Backend (Konzept 1a erwähnt es, nicht Teil dieser Session).
