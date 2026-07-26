# storage-service

Storage-Abstraktionsschicht über austauschbare Backend-Plugins (Konzept 3.6).
Dateiinhalte liegen nie in der relationalen Shared DB — nur Referenz,
Prüfsumme und Größe.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `PUT` | `/objects/{key}` | Hochladen (Body = Rohinhalt, `Content-Type`-Header optional) |
| `GET` | `/objects/{key}` | Herunterladen |
| `DELETE` | `/objects/{key}` | Löschen (Backend + Metadaten) |
| `GET` | `/object-metadata/{key}` | Metadaten (Checksum, Größe, Backend, Zeitstempel) |
| `GET` | `/object-verify/{key}` | Fixity-Check: Prüfsumme neu berechnen und mit Referenzwert vergleichen |
| `GET` | `/healthz` | Health-Check, zeigt aktives Backend |

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

Storage-Redundanz (mehrere Ziele gleichzeitig, Quorum-Schreiben, Fixity-Checks über
alle Kopien) ist **nicht Teil dieser Session** — folgt in P3-S4.

## Lokale Ausführung

```bash
# Lokales Backend (Default)
cd infra && docker compose up -d postgres minio storage-service
curl localhost:8005/healthz

# S3/MinIO-Backend testweise
STORAGE_SERVICE_BACKEND=s3 docker compose up -d --force-recreate storage-service
```

## Tests

```bash
cd infra && docker compose up -d postgres minio && cd ..
uv run pytest services/storage-service/tests
```

`test_local_backend.py` läuft ohne Infrastruktur (nutzt `tmp_path`).
`test_s3_backend.py` braucht echtes MinIO. `test_api.py`/`test_repository.py` brauchen Postgres.
