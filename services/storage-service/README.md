# storage-service

Storage-Abstraktionsschicht über austauschbare Backend-Plugins (Konzept 3.6).
Dateiinhalte liegen nie in der relationalen Shared DB — nur Referenz,
Prüfsumme und Größe.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `PUT` | `/objects/{key}` | Hochladen (Body = Rohinhalt, `Content-Type`-Header optional) |
| `GET` | `/objects/{key}` | Herunterladen (Lese-Fallback über konfigurierte Ziele) |
| `DELETE` | `/objects/{key}` | Löschen (alle Ziele + Metadaten) |
| `GET` | `/object-metadata/{key}` | Metadaten (Checksum, Größe, Backend, Zeitstempel) |
| `GET` | `/objects/{key}/copies` | Kopien-Status je Ziel |
| `GET` | `/object-verify/{key}` | Fixity-Check des Primärziels |
| `GET` | `/object-verify/{key}/all` | Fixity-Check über alle Ziele |
| `POST` | `/replication/process-pending` | Retry-Queue für ausstehende Sekundärkopien verarbeiten |
| `GET` | `/healthz` | Health-Check, zeigt aktive Ziele + Schreibstrategie |

`{key}` erlaubt Schrägstriche (`docs/2026/vertrag.pdf`).

## Backend-Plugins (3.6)

Zwei Implementierungen des `StorageBackend`-Interfaces (schreiben, lesen,
löschen, Existenzprüfung, Prüfsumme), Auswahl über `DMS_BACKEND=local|s3`:

- **`local`** (Default) — lokales Dateisystem unter `DMS_LOCAL_STORAGE_BASE_PATH`.
  **Deckt zugleich den NFS-Fall ab**: In Kubernetes ist dieser Pfad der Mountpunkt
  eines PVC — ob NFS oder Block-Storage darunterliegt, ist für den Code unsichtbar,
  beides verhält sich als normaler Ordner. Ein separates NFS-Backend ist daher
  nicht nötig. Schreibt atomar (temp-Datei + `os.replace`) statt mit
  plattformspezifischem File-Locking, dessen Semantik über NFS-Implementierungen
  hinweg inkonsistent ist.
- **`s3`** — S3-kompatibel (`aioboto3`), Werkseinstellung MinIO für lokale Entwicklung,
  funktioniert identisch gegen AWS S3/Ceph RGW.

## Redundanz (seit P3-S4)

Zwei gleichzeitige Ziele (Primär- + optionales Sekundärziel, je `local`/`s3`) statt
einer generischen Ziel-Menge — Begründung siehe `../../docs/adr/0004-storage-redundancy-scope.md`.

- `DMS_REDUNDANCY_ENABLED=true` + `DMS_SECONDARY_BACKEND=local|s3` (muss sich vom
  Primärziel `DMS_BACKEND` unterscheiden) aktiviert ein zweites Ziel.
- `DMS_WRITE_STRATEGY=quorum|primary_async` (Default `primary_async`) + bei
  `quorum` `DMS_QUORUM_COUNT` (muss ≤ Anzahl konfigurierter Ziele sein).
- Bei `primary_async` bleibt die Sekundärkopie zunächst `pending` und wird erst
  über `POST /replication/process-pending` nachgezogen (Retry-Queue, kein
  In-Prozess-Hintergrundtask).

## Lokale Ausführung

```bash
# Lokales Backend (Default, keine Redundanz)
cd infra && docker compose up -d postgres minio storage-service
curl localhost:8005/healthz

# Mit Redundanz (Quorum über local+s3)
DMS_REDUNDANCY_ENABLED=true DMS_SECONDARY_BACKEND=s3 DMS_WRITE_STRATEGY=quorum DMS_QUORUM_COUNT=2 \
  docker compose up -d --force-recreate storage-service
```

## Tests

```bash
cd infra && docker compose up -d postgres minio && cd ..
uv run pytest services/storage-service/tests
```

`test_local_backend.py` läuft ohne Infrastruktur (nutzt `tmp_path`).
`test_s3_backend.py` braucht echtes MinIO. `test_api.py`/`test_repository.py` brauchen Postgres.
