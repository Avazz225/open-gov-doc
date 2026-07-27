# virus-scan-service

Verpflichtender Virenscan vor Freigabe eines Uploads (Konzept 10.3) + Quarantäne
infizierter Dateien. Wird vom Document Service synchron aufgerufen, *bevor*
Inhalt/Metadaten eines Uploads persistiert werden (siehe
[ADR 0010](../../docs/adr/0010-virus-scan-synchronous-gating.md)).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/scan` | Multipart: `file`, optional `document_id`/`created_by` — führt den Scan durch, legt bei Fund eine Quarantänekopie im Storage Service ab, persistiert das Ergebnis |
| `GET` | `/scans/{id}` | Einzelnes Scan-Ergebnis |
| `GET` | `/scans?document_id=...` | Alle Scans zu einem Dokument |
| `GET` | `/healthz` | Health-Check |

Details/Schema: siehe `../../docs/services/virus-scan-service.md`.

## Engine-Plugins (3.3/3.8, ADR 0010)

Austauschbar über `DMS_SCAN_ENGINE`:
- `eicar` (Standard): erkennt nur die standardisierte EICAR-Testsignatur — kein echter Malware-Schutz, aber deterministisch und ohne externe Abhängigkeit testbar.
- `clamd`: echte Engine gegen einen separat betriebenen `clamd`-Daemon (`DMS_CLAMD_HOST`/`DMS_CLAMD_PORT`) — in dieser Entwicklungsumgebung nicht der Standard, da der initiale Signaturdatenbank-Download (`freshclam`) Minuten dauert und Internetzugriff auf die ClamAV-Mirrors voraussetzt.

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an — Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats storage-service virus-scan-service
curl localhost:8010/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats storage-service && cd ..
uv run pytest services/virus-scan-service/tests
```
