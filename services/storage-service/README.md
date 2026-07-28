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
| `POST` | `/replication/process-pending` | Retry-Queue für ausstehende Kopien verarbeiten |
| `GET` | `/guard-config` | Wächter-Konfiguration lesen (`allow_degraded_start`) |
| `PUT` | `/guard-config` | Wächter-Konfiguration ändern (wirkt erst beim nächsten Start) |
| `GET` | `/guard-status` | Geräte-ID/Status je Ziel (Admin-UI-Statusblock) |
| `POST` | `/guard-status/{target_id}/reidentify` | Beabsichtigten Datenträger-Wechsel akzeptieren, ohne Neustart |
| `GET` | `/healthz` | Health-Check, zeigt aktive Ziele + Schreibstrategie |

`{key}` erlaubt Schrägstriche (`docs/2026/vertrag.pdf`).

## Backend-Plugins (3.6)

Zwei Implementierungen des `StorageBackend`-Interfaces (schreiben, lesen,
löschen, Existenzprüfung, Prüfsumme):

- **`local`** — lokales Dateisystem unter dem je Ziel konfigurierten `base_path`.
  **Deckt zugleich den NFS-Fall ab**: In Kubernetes ist dieser Pfad der Mountpunkt
  eines PVC — ob NFS oder Block-Storage darunterliegt, ist für den Code unsichtbar,
  beides verhält sich als normaler Ordner. Ein separates NFS-Backend ist daher
  nicht nötig. Schreibt atomar (temp-Datei + `os.replace`) statt mit
  plattformspezifischem File-Locking, dessen Semantik über NFS-Implementierungen
  hinweg inkonsistent ist.
- **`s3`** — S3-kompatibel (`aioboto3`), Werkseinstellung MinIO für lokale Entwicklung,
  funktioniert identisch gegen AWS S3/Ceph RGW.

## Ziel-Set (seit P5b-S6)

`DMS_TARGETS` ist eine JSON-Liste von `{id, type, ...typspezifische Felder}` -
beliebig viele Einträge, auch mehrere desselben `type` (z. B. zwei S3-Provider),
da `id` und nicht `type` der eindeutige Schlüssel ist. Ersetzt die frühere feste
`DMS_BACKEND`/`DMS_SECONDARY_BACKEND`-Struktur, siehe
`../../docs/adr/0004-storage-redundancy-scope.md` und
`../../docs/adr/0017-storage-device-identity-guard.md`.

```bash
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/tmp/dms-storage-dev"}]'
```

- `DMS_WRITE_STRATEGY=quorum|primary_async` (Default `primary_async`) + bei
  `quorum` `DMS_QUORUM_COUNT` (muss ≤ Anzahl konfigurierter Ziele sein).
- Bei `primary_async` bleibt jede Kopie außer der des Primärziels zunächst
  `pending` und wird erst über `POST /replication/process-pending` nachgezogen
  (Retry-Queue, kein In-Prozess-Hintergrundtask).

## Datenträger-Wechsel-Wächter (seit P5b-S6)

Jedes Ziel bekommt eine generierte Geräte-ID (Marker-Objekt unter dem
reservierten Key `__dms_storage_identity__`), abgeglichen gegen den in der
Shared DB (`backend_identity`) hinterlegten Referenzwert bei jedem Start.
Werkseinstellung: Startverweigerung bei Abweichung/Nichterreichbarkeit. Admin-
Override `PUT /guard-config {"allow_degraded_start": true}` erlaubt einen
degradierten Start, sofern mindestens ein Ziel nachweislich unverändert ist -
danach automatische Vormerkung zur Nachreplikation (`POST
/replication/process-pending`). Details siehe
`../../docs/adr/0017-storage-device-identity-guard.md`.

**Rebalancing + Korrekturmechanismus (seit P5c-S2)**: ein neu zum Ziel-Set
hinzugefügtes Ziel bekommt beim Erststart-Bootstrap automatisch `pending`-
Kopien für alle bereits existierenden Objekte (kein separater Trigger nötig -
eine Ziel-Set-Änderung erfordert ohnehin einen Neustart). Ein beabsichtigter
Datenträger-Wechsel lässt sich zur Laufzeit über `POST
/guard-status/{target_id}/reidentify` akzeptieren (übernimmt eine vorhandene
Marker-Datei des neuen Geräts oder prägt eine neue, setzt bestehende Kopien
auf `pending` zurück) - ersetzt die zuvor nötige direkte Korrektur in
`backend_identity`.

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an (Heartbeat, Deregister beim Shutdown) - Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, siehe `docs/services/gateway-service.md` für den Konsumenten (API-Gateway, dynamisches Routing).

## Lokale Ausführung

```bash
# Ein lokales Ziel (Default, keine Redundanz)
cd infra && docker compose up -d postgres minio storage-service
curl localhost:8005/healthz

# Mit Redundanz (Quorum über local+s3)
STORAGE_SERVICE_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"s3-minio","type":"s3","endpoint_url":"http://minio:9000","access_key":"dms_minio",
   "secret_key":"dms_minio_dev_only","bucket":"dms-storage","region":"us-east-1"}]' \
  STORAGE_SERVICE_WRITE_STRATEGY=quorum STORAGE_SERVICE_QUORUM_COUNT=2 \
  docker compose up -d --force-recreate storage-service
```

## Tests

```bash
cd infra && docker compose up -d postgres minio && cd ..
uv run pytest services/storage-service/tests
```

`test_local_backend.py`/`test_backend_factory.py` laufen ohne Infrastruktur
(nutzen `tmp_path`). `test_s3_backend.py` braucht echtes MinIO.
`test_api.py`/`test_repository.py`/`test_identity_guard.py` brauchen Postgres
(**86 Tests** insgesamt).
