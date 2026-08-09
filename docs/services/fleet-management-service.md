# fleet-management-service

**Verantwortung:** Übergeordnete, installationsunabhängige Verwaltungsebene für mehrere DMS-Installationen (Konzept 3a "Zentrale Verwaltungsebene, optional, separater Baustein"). Kein interner Service einer einzelnen Installation - ein Betreiber, der mehrere Installationen betreibt/überblickt, deployt diesen Service unabhängig davon (gleiches Architekturmuster wie `federation-hub-service`, ADR 0028).

**Konzept-Referenz:** 3a
**Eigenes Postgres-Schema:** `fleet` (Tabelle `managed_installation`)

## Architekturentscheidungen

Siehe [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md) für die vollständige Begründung. Kurzfassung:

- **Drei Fähigkeiten, wörtlich aus 3a**: Health-/Lizenz-Überblick (`GET /installations/status`), Lizenzvergabe/-verlängerung (`POST /installations/{id}/license`), zentrales Provisioning aus einer Konfigurationsvorlage (`POST /installations/{id}/provision`).
- **Erreicht jede verwaltete Installation ausschließlich über deren Gateway** (`{gateway_base_url}/api/{service}/...`), nie eine interne Container-Adresse - und ausschließlich `registry-service`/`license-service`/`config-service`, nie `document-service`/`folder-service` (3a: "keinen Zugriff auf Dokumenteninhalte einzelner Installationen").
- **Authentisierung über einen dedizierten, installationsweiten `DMS_FLEET_AGENT_API_KEY`** statt RBAC - dieser Service hat keinen Keycloak-Principal in irgendeiner verwalteten Installation. Dieselbe API-Key-statt-RBAC-Linie wie `federation-hub-service`/`migration-service`.
- **Reiner Durchreicher, keine Vorlagen-Bibliothek**: `POST /installations/{id}/provision` nimmt ein rohes 7.3-Konfigurationsdokument entgegen (z. B. der Export einer Referenzinstallation) - eine kuratierte Paket-Bibliothek ist Konzept §14/Phase 17, nicht Teil dieser Session.
- **`fleet_agent_api_key` im Klartext gespeichert** (nicht gehasht) - dieser Service muss ihn bei jedem ausgehenden Aufruf präsentieren, nie selbst verifizieren (identische Begründung wie `migration-service`s `PairedInstallation`, ADR 0034).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/healthz` | Eigener Health-Check |
| `POST` | `/installations` | Verwaltete Installation registrieren - `fleet_agent_api_key` optional (sonst generiert), nur in dieser Antwort im Klartext enthalten |
| `GET` | `/installations` | Liste aller verwalteten Installationen, ohne Schlüssel |
| `DELETE` | `/installations/{id}` | Verwaltete Installation entfernen |
| `GET` | `/installations/{id}/status` | Live-Statusabruf einer Installation (Identität + Lizenzstatus über deren Gateway) - `reachable=false` statt Exception bei Netzwerk-/Auth-Fehlern |
| `GET` | `/installations/status` | Statusabruf aller verwalteten Installationen, parallel (`asyncio.gather`) |
| `POST` | `/installations/{id}/license` | Lizenztoken an die Ziel-Installation weiterreichen (`POST .../license` über deren Gateway) |
| `POST` | `/installations/{id}/provision` | Konfigurationsdokument an die Ziel-Installation weiterreichen (`POST .../config/import` über deren Gateway) |

## Datenmodell

`fleet.managed_installation`: `id` (PK, UUID), `display_name`, `gateway_base_url`, `fleet_agent_api_key` (Klartext), `created_at`, `updated_at`.

## Gegenstück auf der verwalteten Installation

Keine neue Rolle/kein neuer Service auf der Zielseite - drei bestehende Endpunkte erweitert bzw. wiederverwendet:

- `registry-service` `GET /installation` (P13-S1, unverändert ungegatet) - liefert `{id, display_name}`.
- `license-service` `GET /license/status` (P9-S2, unverändert ungegatet) sowie `POST /license` (neu: akzeptiert zusätzlich `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` statt RBAC, siehe dortiges `_is_fleet_agent()`).
- `config-service` `POST /config/import` (neu: derselbe Bypass, siehe dortiges `_is_fleet_agent()`).
- `gateway-service.settings.public_routes` (neu: vier Einträge, damit diese Pfade ohne Keycloak-Token durch den Gateway erreichbar sind - die eigentliche Absicherung der beiden Schreib-Pfade bleibt bei den Zielservices selbst).

## Selbst-Registrierung

Keine - dieser Service gehört zu keiner einzelnen Installation, daher kein `dms-registry-client`-Aufruf (`DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS` bleiben ungenutzt), kein Event-Bus-Producer/-Consumer.

## Tests

`services/fleet-management-service/tests/` - 13 Tests, In-Prozess-`TestClient` gegen einen ASGI-Stub der Ziel-Installation (`httpx.ASGITransport`, gleiches Muster wie `federation-hub-service`): Installations-CRUD inkl. Schlüssel-nur-einmal-Ausgabe, Status-Abruf (erreichbar/nicht erreichbar), Aggregation über mehrere Installationen, Lizenz-Push, Provisionierung, 404-Fälle. Zusätzlich live gegen den laufenden Docker-Compose-Stack verifiziert (Selbst-Loopback: eine `managed_installation` zeigt auf den eigenen `gateway-service` desselben Stacks).

## Offene Punkte

- Keine Schlüssel-Rotation (siehe ADR 0037) - ein kompromittierter `fleet_agent_api_key` erfordert manuelles Ändern auf beiden Seiten.
- Keine Vorlagen-Bibliothek für `POST .../provision` - reiner Durchreicher, kuratierte Pakete folgen erst mit Phase 17 (Konzept §14).
- Keine proaktive Benachrichtigung bei Lizenzablauf/-überschreitung über die Fleet-Ebene - rein Pull-basiert (`GET .../status`), kein Push/Webhook von der verwalteten Installation zurück an diesen Service.
