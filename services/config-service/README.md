# config-service

Konfigurationsimport/-export (Konzept 7.3, P12-S3): vollständige Systemkonfiguration
(Objekttypen inkl. Formular-Layouts, Workflows, Rollen-Templates, Vier-Augen-Konfiguration,
Sensor-Konfiguration) als ein JSON-Dokument exportierbar und in ein anderes (oder dasselbe, z. B.
Staging→Produktion) System re-importierbar. Seit **P14-S1** zusätzlich eine Delta-/
Vergleichsfunktion zwischen zwei Exporten (7.5, konfigurierbare Ignore-Regex für abweichende
Namenskonventionen). Reiner Orchestrator ohne eigenes Postgres-Schema — Details siehe
[`docs/services/config-service.md`](../../docs/services/config-service.md).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/config/export` | Konfigurationsdokument exportieren, optional `?categories=roles&categories=...` |
| `POST` | `/config/compare` | Delta-/Vergleichsfunktion zwischen zwei Exporten (7.5, P14-S1) — ungegated, rein lesend |
| `POST` | `/config/import` | Konfigurationsdokument importieren (Upsert je Kategorie) — gegated hinter `admin.object_config`, `X-DMS-Principal`-Header nötig |
| `GET` | `/healthz` | Health-Check |

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres object-type-service workflow-service permission-service monitoring-service registry-service config-service
curl localhost:8029/healthz
```

## Tests

Läuft wie `webdav-connector`/`migration-service` gegen den echten, laufenden Container (kein
In-Prozess-`TestClient`, kein Mocking der Nachbar-Services):

```bash
cd infra && docker compose up -d postgres object-type-service workflow-service permission-service monitoring-service registry-service config-service
cd ..
uv run pytest services/config-service/tests
```
