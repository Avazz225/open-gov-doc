# fleet-management-service

**Verantwortung:** Übergeordnete, installationsunabhängige Verwaltungsebene für mehrere DMS-Installationen (Konzept 3a "Zentrale Verwaltungsebene, optional, separater Baustein"). Kein interner Service einer einzelnen Installation - ein Betreiber, der mehrere Installationen betreibt/überblickt, deployt diesen Service unabhängig davon (gleiches Architekturmuster wie `federation-hub-service`, ADR 0028).

Seit P13-S2b zusätzlich **Flotten-Update-Orchestrierung** (3a-Erweiterung): benannte Installationsgruppen/Wellen, versionierte Update-Pläne, gestaffelte Rollouts mit der Konzept-eigenen fünfwertigen Fehlerentscheidung.

**Konzept-Referenz:** 3a
**Eigenes Postgres-Schema:** `fleet` (Tabellen `managed_installation`, `installation_group`, `installation_group_member`, `update_plan`, `rollout`, `installation_run`)

## Architekturentscheidungen

Siehe [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md) für die vollständige Begründung. Kurzfassung:

- **Drei Fähigkeiten, wörtlich aus 3a**: Health-/Lizenz-Überblick (`GET /installations/status`), Lizenzvergabe/-verlängerung (`POST /installations/{id}/license`), zentrales Provisioning aus einer Konfigurationsvorlage (`POST /installations/{id}/provision`).
- **Erreicht jede verwaltete Installation ausschließlich über deren Gateway** (`{gateway_base_url}/api/{service}/...`), nie eine interne Container-Adresse - und ausschließlich `registry-service`/`license-service`/`config-service`, nie `document-service`/`folder-service` (3a: "keinen Zugriff auf Dokumenteninhalte einzelner Installationen").
- **Authentisierung über einen dedizierten, installationsweiten `DMS_FLEET_AGENT_API_KEY`** statt RBAC - dieser Service hat keinen Keycloak-Principal in irgendeiner verwalteten Installation. Dieselbe API-Key-statt-RBAC-Linie wie `federation-hub-service`/`migration-service`.
- **Reiner Durchreicher, keine Vorlagen-Bibliothek**: `POST /installations/{id}/provision` nimmt ein rohes 7.3-Konfigurationsdokument entgegen (z. B. der Export einer Referenzinstallation) - eine kuratierte Paket-Bibliothek ist Konzept §14/Phase 17, nicht Teil dieser Session.
- **`fleet_agent_api_key` im Klartext gespeichert** (nicht gehasht) - dieser Service muss ihn bei jedem ausgehenden Aufruf präsentieren, nie selbst verifizieren (identische Begründung wie `migration-service`s `PairedInstallation`, ADR 0034).

## Flotten-Update-Orchestrierung (3a-Erweiterung, P13-S2b)

Siehe [ADR 0038](../adr/0038-fleet-update-orchestration-external-gates-not-remote-control.md) für die vollständige Begründung. Kurzfassung:

- **Wellen/Gruppen**: `InstallationGroup` fasst Installationen benannt zusammen; ein `Rollout` wendet einen `UpdatePlan` auf Gruppenmitglieder ∪ `include` − `exclude` an und muss explizit gestartet werden (`POST /rollouts/{id}/start`) - keine automatische Kettenreaktion zur nächsten Welle, die ist ein eigener, separat angelegter `Rollout`.
- **Deklarativer, versionierter Update-Plan**: `UpdatePlan.steps` ist eine geordnete Liste von `{name, step_type, requires_approval}`. Zwei `step_type`-Werte: `"verify"` (automatisch, fragt `_fetch_status()` der Zielinstallation ab) und `"gate"` (durch `POST .../mark-done` bestätigt - steht für jede Aktion außerhalb dieses Service: Bereichssperre/Wartungsmodus setzen, den Rolling Update durchführen, ein Backup ziehen, final freigeben).
- **Bewusste Grenze**: `fleet-management-service` löst `gate`-Schritte **nicht selbst** per HTTP auf der Zielinstallation aus (siehe ADR 0038 - `permission-service`s Scope-Lock-/Wartungsmodus-Endpunkte haben keine sichere, RBAC-unabhängige Fernsteuerungs-Öffnung wie `license-service`/`config-service`, und 10.4/10.5 sind ohnehin bewusst Skripte, keine Services). Der Bediener, der die Aktion durchgeführt hat, bestätigt sie über die Fleet-Konsole.
- **Fünfwertige Fehlerentscheidung** (3a wörtlich) als `InstallationRun.status`: `retry_later`/`wait_external`/`manual_required`/`recoverable_failed`/`fatal_contract`, plus die zwei strukturellen Zustände `pending`/`completed`. `mark-done` lässt den Bediener jede der vier meldbaren Entscheidungen wählen (`success` ist der Default).
- **Vier-Augen (4.3) als struktureller Vorschlag-/Freigabe-Fluss**: ein `requires_approval`-Schritt geht nach `mark-done` in `manual_required` über (`proposed_by` gespeichert); `approve` verlangt `actor != proposed_by`. Keine echte Zwei-Identitäten-Kryptografie (dieser Service führt keine eigene Nutzerverwaltung), siehe ADR 0038.
- **`retry`** für `retry_later`/`recoverable_failed` (setzt denselben Schritt zurück in den Wartezustand); **`acknowledge-fatal`** für `fatal_contract` (eigener, expliziter Endpunkt statt `retry` - erzwingt eine bewusste Bestätigung, dass Plan/Konfiguration korrigiert wurden).

### API (Orchestrierung)

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/groups` | Gruppe/Welle anlegen |
| `GET` | `/groups` | Liste inkl. Mitglieds-Installations-IDs |
| `POST` | `/groups/{id}/members` | Installation zur Gruppe hinzufügen |
| `DELETE` | `/groups/{id}/members/{installation_id}` | Aus der Gruppe entfernen |
| `DELETE` | `/groups/{id}` | Gruppe löschen |
| `POST` | `/plans` | Update-Plan anlegen (`422` bei unbekanntem `step_type`/leeren Schritten) |
| `GET` | `/plans`, `GET /plans/{id}` | Liste/Detail |
| `POST` | `/rollouts` | Welle anlegen (`422` bei leerem Zielkreis) - Status `draft` |
| `GET` | `/rollouts`, `GET /rollouts/{id}` | Liste/Detail inkl. aller `InstallationRun`s |
| `POST` | `/rollouts/{id}/start` | Explizit starten (`409` wenn nicht `draft`) |
| `POST` | `/rollouts/{id}/installations/{iid}/advance` | Nur für `"verify"`-Schritte - automatischer Check |
| `POST` | `/rollouts/{id}/installations/{iid}/mark-done` | Nur für `"gate"`-Schritte - Ergebnis melden |
| `POST` | `/rollouts/{id}/installations/{iid}/approve`, `.../reject` | Vier-Augen-Entscheidung bei `manual_required` |
| `POST` | `/rollouts/{id}/installations/{iid}/retry` | Für `retry_later`/`recoverable_failed` |
| `POST` | `/rollouts/{id}/installations/{iid}/acknowledge-fatal` | Für `fatal_contract` |

## API (Fleet-/Lizenz-Grundfunktionen, P13-S2)

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

Seit P13-S2b zusätzlich: `installation_group` (`id`, `name` unique, `created_at`), `installation_group_member` (`group_id`+`installation_id`, Composite-PK), `update_plan` (`id`, `name`, `version`, `steps` JSON, `created_at`), `rollout` (`id`, `plan_id`, `name`, `group_id` nullable, `include`/`exclude` JSON-Listen, `status`, `created_at`, `started_at`, `started_by`), `installation_run` (`id`, `rollout_id`, `installation_id`, `current_step_index`, `status`, `last_outcome`, `error_message`, `proposed_by`, `started_at`, `updated_at`, `completed_at`).

## Gegenstück auf der verwalteten Installation

Keine neue Rolle/kein neuer Service auf der Zielseite - drei bestehende Endpunkte erweitert bzw. wiederverwendet:

- `registry-service` `GET /installation` (P13-S1, unverändert ungegatet) - liefert `{id, display_name}`.
- `license-service` `GET /license/status` (P9-S2, unverändert ungegatet) sowie `POST /license` (neu: akzeptiert zusätzlich `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` statt RBAC, siehe dortiges `_is_fleet_agent()`).
- `config-service` `POST /config/import` (neu: derselbe Bypass, siehe dortiges `_is_fleet_agent()`).
- `gateway-service.settings.public_routes` (neu: vier Einträge, damit diese Pfade ohne Keycloak-Token durch den Gateway erreichbar sind - die eigentliche Absicherung der beiden Schreib-Pfade bleibt bei den Zielservices selbst).

## Selbst-Registrierung

Keine - dieser Service gehört zu keiner einzelnen Installation, daher kein `dms-registry-client`-Aufruf (`DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS` bleiben ungenutzt), kein Event-Bus-Producer/-Consumer.

## Tests

`services/fleet-management-service/tests/` - 26 Tests (seit P13-S2b, vorher 13), In-Prozess-`TestClient` gegen einen ASGI-Stub der Ziel-Installation (`httpx.ASGITransport`, gleiches Muster wie `federation-hub-service`): Installations-CRUD inkl. Schlüssel-nur-einmal-Ausgabe, Status-Abruf (erreichbar/nicht erreichbar), Aggregation über mehrere Installationen, Lizenz-Push, Provisionierung, 404-Fälle, sowie (P13-S2b) Gruppen-Mitgliedschaft, Plan-Validierung, Rollout-Auflösung (Gruppe/include/exclude, leerer Zielkreis), kompletter Rollout-Happy-Path über alle Schritttypen inkl. Vier-Augen, `recoverable_failed`→`retry`, `fatal_contract`→`acknowledge-fatal`, `verify` bei nicht erreichbarer Installation → `retry_later`, Ablehnung einer Freigabe. Die P13-S2-Fähigkeiten zusätzlich live gegen den laufenden Docker-Compose-Stack verifiziert (Selbst-Loopback).

## Offene Punkte

- Keine Schlüssel-Rotation (siehe ADR 0037) - ein kompromittierter `fleet_agent_api_key` erfordert manuelles Ändern auf beiden Seiten.
- Keine Vorlagen-Bibliothek für `POST .../provision` - reiner Durchreicher, kuratierte Pakete folgen erst mit Phase 17 (Konzept §14).
- Keine proaktive Benachrichtigung bei Lizenzablauf/-überschreitung über die Fleet-Ebene - rein Pull-basiert (`GET .../status`), kein Push/Webhook von der verwalteten Installation zurück an diesen Service.
- **`gate`-Schritte sind nicht ferngesteuert, nur bestätigt** (siehe ADR 0038) - `fleet-management-service` verifiziert nicht, dass eine Bereichssperre/ein Backup/ein Rolling Update tatsächlich stattgefunden hat, bevor `mark-done` den Schritt als erledigt markiert. Eine engere, aktions-/installationsspezifische Fernsteuerung bleibt ein möglicher, im Detail zu entwerfender Ausbauschritt.
- **Vier-Augen ohne eigene Nutzerverwaltung** (ADR 0038) - `approve` erzwingt nur `actor != proposed_by` als Freitext-Vergleich, keine echte, kryptografisch verankerte Zwei-Identitäten-Prüfung.
- Kein Sensor-/Prometheus-Export für Rollout-Fortschritt (3a nennt "über das bestehende Monitoring beobachtbar" als Ziel) - aktuell nur über `GET /rollouts/{id}` abfragbar, keine `/metrics`-Integration.
