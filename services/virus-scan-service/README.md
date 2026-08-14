# virus-scan-service

Mandatory virus scan before an upload is approved (Concept 10.3) + quarantine
of infected files. Called synchronously by the Document Service *before*
the content/metadata of an upload is persisted (see
[ADR 0010](../../docs/adr/0010-virus-scan-synchronous-gating.md)).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scan` | Multipart: `file`, optional `document_id`/`created_by` — performs the scan, stores a quarantine copy in the Storage Service if a hit is found, persists the result |
| `GET` | `/scans/{id}` | Single scan result |
| `GET` | `/scans?document_id=...` | All scans for a document |
| `GET` | `/healthz` | Health check |

Details/schema: see `../../docs/services/virus-scan-service.md`.

## Engine Plugins (3.3/3.8, ADR 0010)

Interchangeable via `DMS_SCAN_ENGINE`:
- `eicar` (default): detects only the standardized EICAR test signature — not real malware protection, but deterministic and testable without an external dependency.
- `clamd`: real engine against a separately operated `clamd` daemon (`DMS_CLAMD_HOST`/`DMS_CLAMD_PORT`) — not the default in this development environment, since the initial signature database download (`freshclam`) takes minutes and requires internet access to the ClamAV mirrors.

## Registry Registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` — opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Running Locally

```bash
cd infra && docker compose up -d postgres nats storage-service virus-scan-service
curl localhost:8010/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats storage-service && cd ..
uv run pytest services/virus-scan-service/tests
```
