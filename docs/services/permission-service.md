# permission-service

**Verantwortung:** RBAC — Rollen, Zuweisungen an Principals (Nutzer/Gruppen) an Ressourcen, Vererbung entlang einer Ressourcen-Hierarchie, materialisierter ereignisgetriebener Rechte-Cache (Konzept 4.1).

**Konzept-Referenz:** 4.1
**Eigenes Postgres-Schema:** `permission` (Tabellen `role`, `role_assignment`, `resource_node`, `effective_permission_cache`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/roles` | Rolle anlegen |
| `GET` | `/roles` | Alle Rollen |
| `POST` | `/role-assignments` | Zuweisung anlegen (404 bei unbekannter Rolle/Ressource) |
| `DELETE` | `/role-assignments/{id}` | Zuweisung entfernen |
| `GET` | `/resources/{id}` | Ressourcenknoten lesen |
| `PATCH` | `/resources/{id}` | `inherit` umschalten |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Gecachte effektive Rollen/Rechte |
| `GET` | `/check` | `{allowed: bool}` für eine konkrete Permission |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `resource_node`: `resource_id` (PK), `parent_id` (self-FK, nullable), `resource_type`, `inherit` (bool). Wurzelknoten `"root"` wird beim Start idempotent angelegt.
- `role`: `id`, `name` (unique), `description`, `permissions` (JSON-Liste von Capability-Strings, z. B. `["read","write"]`).
- `role_assignment`: `principal_type` (`user`|`group`), `principal_id`, `role_id`, `resource_id` — unique auf der Kombination.
- `effective_permission_cache`: `(principal_id, resource_id)` → `roles`, `permissions`, `computed_at`. Wird bei jeder Rechte-/Strukturänderung **vollständig geleert** (bewusste Vereinfachung, siehe README) statt granular je Teilbaum invalidiert.

## Vererbungsalgorithmus

Von der angefragten Ressource aus wird die Vorfahrenkette (`parent_id`) nach oben durchlaufen, an jedem Knoten werden die Zuweisungen des Principals gesammelt. Ein Knoten mit `inherit=false` beendet den Aufstieg **nach** Auswertung seiner eigenen Zuweisungen — Standard-DMS-Verhalten (SharePoint/Alfresco), wie in Konzept 4.1 gefordert.

## Struktur-Synchronisation (provisorischer Vertrag)

Der Folder Service (P3-S3) existiert noch nicht. `structure_consumer.py` abonniert bereits `settings.structure_subjects` (Default `["folder.>"]`) über `NatsEventBusClient(ensure_stream=False)` und erwartet folgendes Event-Schema:

| event_type (Suffix) | payload |
|---|---|
| `*.resource.created` | `{resource_id, parent_id, resource_type}` |
| `*.resource.moved` | `{resource_id, new_parent_id}` |
| `*.resource.deleted` | `{resource_id}` |

**Dieser Vertrag ist provisorisch** — Subject-Präfix (`folder`) und Payload-Form können sich ändern, sobald der Folder Service tatsächlich gebaut wird; dann wird dieser Abschnitt aktualisiert.

**Bekannte Grenze**: Existiert beim Start noch kein Stream für ein konfiguriertes Subject (kein Producer je gelaufen), wird das Subject übersprungen (`SubjectNotFoundError` abgefangen, siehe `dms-eventbus-client`/ADR 0001) statt den Service-Start zu blockieren — es gibt aber keinen Retry-Loop, der den Stream später automatisch nachzieht; ein Neustart ist dann nötig.

## Events

**Konsumiert:** `folder.>` (provisorisch, s. o.).
**Publiziert:** keine — reiner RBAC-Dienst, keine eigenen Domain-Events.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Gruppenmitgliedschaft wird nicht aufgelöst: `principal_id` einer Zuweisung muss exakt dem abgefragten Principal entsprechen. Eine Auflösung "Nutzer X ist Mitglied von Gruppe Y, die Rolle Z hat" ist nicht Teil dieser Session (hängt von AD-Gruppen-Sync im Auth Service, 4.4, ab, der ebenfalls noch offen ist).
- Granularere Cache-Invalidierung (nur betroffener Teilbaum statt gesamter Cache) als spätere Optimierung möglich, ohne die API zu ändern.
- Konfigurierbares Vier-Augen-Prinzip (4.3) für Rechteänderungen ist nicht Teil dieser Session.
