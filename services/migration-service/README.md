# migration-service

Migration/Transfer Service (Konzept 7.2, P12-S2): Sperren → Kopieren → Verifizieren →
Freigabe im Zielsystem → Löschung im Quellsystem nach Übergangsfrist, zwischen zwei
direkt gepaarten Installationen dieser Software (kein Hub, siehe ADR in
`docs/adr/`). Läuft selbst als auditierbarer, resumable Workflow über
`workflow-service` — Details siehe
[`docs/services/migration-service.md`](../../docs/services/migration-service.md).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/paired-installations` | Ziel-/Quell-Installation paaren (API-Key einmalig zurückgegeben) |
| `GET`/`DELETE` | `/paired-installations[/{id}]` | Auflisten/Entfernen |
| `POST` | `/transfers` | Transfer starten (Vier-Augen-fähig, 4.3) |
| `GET` | `/transfers[/{id}]` | Status/Liste |
| `POST` | `/transfers/{id}/steps/*` | Intern — Ziel der `connector_call`-Service-Tasks in `resources/*.bpmn` |
| `POST` | `/inbound/transfers/*` | Zielseite — von einer gepaarten Quelle aufgerufen, `Authorization: Bearer <api_key>` |
| `GET` | `/healthz` | Health-Check |

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats document-service folder-service permission-service workflow-service registry-service migration-service
curl localhost:8028/healthz
```

## Tests

Läuft wie `webdav-connector` gegen den echten, laufenden Container (Selbst-Loopback-
Smoke-Test statt einer zweiten echten Installation, siehe `docs/services/migration-service.md`):

```bash
cd infra && docker compose up -d postgres nats document-service folder-service permission-service workflow-service registry-service migration-service
cd ..
uv run pytest services/migration-service/tests
```
