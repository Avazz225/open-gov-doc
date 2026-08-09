# fleet-management-service

Übergeordnete, installationsunabhängige Verwaltungsebene für mehrere DMS-Installationen (Konzept 3a, P13-S2).

Siehe [`docs/services/fleet-management-service.md`](../../docs/services/fleet-management-service.md) für Endpunkte/Architekturentscheidungen.

## Kurzfassung

- **Kein interner Service einer Installation** - wie `federation-hub-service` (ADR 0028) ein unabhängig betriebenes Werkzeug für einen Betreiber, der mehrere Installationen überblickt. Kein Zugriff auf Dokumenteninhalte - nur auf `registry-service`/`license-service`/`config-service` einer verwalteten Installation, über deren Gateway.
- **Drei Fähigkeiten** (3a wörtlich): Health-/Lizenz-Überblick (`GET /installations/status`), Lizenzvergabe (`POST /installations/{id}/license`), zentrales Provisioning aus einer Konfigurationsvorlage (`POST /installations/{id}/provision`).
- Authentisiert sich gegenüber einer verwalteten Installation über einen dort per `DMS_FLEET_AGENT_API_KEY` konfigurierten, installationsweiten Schlüssel (P13-S1/S2) - kein Keycloak-Principal dieser Installation nötig.

## Tests

```bash
uv run --package fleet-management-service pytest services/fleet-management-service/tests
```
