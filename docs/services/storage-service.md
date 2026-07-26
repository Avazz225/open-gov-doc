# storage-service

**Verantwortung:** Storage-Abstraktionsschicht über austauschbare Backend-Plugins — Dateiinhalte liegen ausschließlich in einem konfigurierten Backend, die Shared DB hält nur Metadaten (Referenz, Prüfsumme, Größe) (Konzept 3.6).

**Konzept-Referenz:** 3.6
**Eigenes Postgres-Schema:** `storage` (Tabelle `object_metadata`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `PUT` | `/objects/{key:path}` | Hochladen, berechnet SHA-256, schreibt ins aktive Backend, upserted Metadaten |
| `GET` | `/objects/{key:path}` | Herunterladen (404, wenn Metadaten oder Backend-Objekt fehlen) |
| `DELETE` | `/objects/{key:path}` | Löschen (Backend zuerst, idempotent, dann Metadaten) |
| `GET` | `/object-metadata/{key:path}` | Metadaten lesen |
| `GET` | `/object-verify/{key:path}` | Fixity-Check-Basis: Prüfsumme neu aus dem Backend lesen, mit Referenzwert vergleichen |
| `GET` | `/healthz` | Health-Check inkl. aktivem Backend |

## Backend-Plugin-Interface (3.6)

`StorageBackend` (ABC): `write`, `read`, `delete`, `exists`, `checksum`. Zwei Implementierungen in dieser Session:

- **`LocalFilesystemBackend`** — deckt sowohl "lokales Dateisystem" als auch **NFS** ab: In Kubernetes ist der konfigurierte Pfad der Mountpunkt eines PVC, das Backend sieht nur einen Ordner, unabhängig davon, ob NFS oder Block-Storage dahintersteht. Schreibt atomar (temporäre Datei + `os.replace`) statt mit plattformspezifischem `fcntl`-Locking — dessen Semantik ist über NFS-Implementierungen hinweg inkonsistent, während atomares Rename-nach-Schreiben auf NFSv4+ zuverlässig funktioniert und Teilschreib-Korruption bei gleichzeitigen Schreibern auf denselben Key verhindert. Nebenläufige *Bearbeitung* eines Dokuments ist Aufgabe des Document-Service-Lockings (4.2), nicht dieser Schicht.
- **`S3Backend`** — `aioboto3`, Werkseinstellung MinIO, funktioniert identisch gegen jeden S3-kompatiblen Provider.

Beide sind unabhängig getestet: `LocalFilesystemBackend` gegen echtes Dateisystem (`tmp_path`), `S3Backend` gegen echtes MinIO (nicht gemockt).

## Datenmodell

`object_metadata`: `object_key` (PK), `backend`, `checksum_sha256`, `size_bytes`, `content_type`, `created_at`, `updated_at`.

## Events

Keine — Storage Service publiziert/konsumiert in dieser Session keine Events. Wird relevant, sobald Document Service (P3-2) darauf aufsetzt.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte (bewusst auf P3-S4 verschoben)

- **Storage-Redundanz**: mehrere gleichzeitige Ziele ("Storage-Ziel-Set"), Quorum-Schreiben vs. synchron-primär/asynchron-sekundär, konfigurierbar je Objekttyp/Ordner.
- **Automatisierter Fixity-Check über alle Kopien** (regelmäßiger Lauf) — `/object-verify` liefert nur die Grundlage (Einzelabfrage on-demand), nicht die Automatisierung.
- **Rebalancing** beim Hinzufügen/Entfernen eines Backends aus einem Ziel-Set.
- Azure-Blob-Backend (Konzept 1a erwähnt es, nicht Teil dieser Session).
