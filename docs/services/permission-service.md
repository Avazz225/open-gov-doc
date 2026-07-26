# permission-service

**Verantwortung:** RBAC — Rollen, Zuweisungen an Principals (Nutzer/Gruppen) an Ressourcen, Vererbung entlang einer Ressourcen-Hierarchie, materialisierter ereignisgetriebener Rechte-Cache (Konzept 4.1). Seit P3-S4 zusätzlich Bereichssperren (4.7): temporäre, RBAC-überlagernde Sperrung ganzer Ressourcen-Teilbäume.

**Konzept-Referenz:** 4.1, 4.7
**Eigenes Postgres-Schema:** `permission` (Tabellen `role`, `role_assignment`, `resource_node`, `effective_permission_cache`, `scope_lock`)

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
| `GET` | `/check?...&access_type=read\|write` | Autorisierungs-Check inkl. Bereichssperren-Überlagerung |
| `POST` | `/scope-locks` | Bereichssperre setzen (404 bei unbekannter Ressource) |
| `DELETE` | `/scope-locks/{id}` | Bereichssperre aufheben (`released_by`) |
| `GET` | `/scope-locks?resource_id=` | Sperren auflisten (optional gefiltert) |
| `GET` | `/scope-locks/effective/{resource_id}` | Aktive Sperren, die diese Ressource betreffen (inkl. geerbte) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `resource_node`: `resource_id` (PK), `parent_id` (self-FK, nullable), `resource_type`, `inherit` (bool). Wurzelknoten `"root"` wird beim Start idempotent angelegt.
- `role`: `id`, `name` (unique), `description`, `permissions` (JSON-Liste von Capability-Strings, z. B. `["read","write"]`).
- `role_assignment`: `principal_type` (`user`|`group`), `principal_id`, `role_id`, `resource_id` — unique auf der Kombination.
- `effective_permission_cache`: `(principal_id, resource_id)` → `roles`, `permissions`, `computed_at`. Wird bei jeder Rechte-/Strukturänderung **vollständig geleert** (bewusste Vereinfachung, siehe README) statt granular je Teilbaum invalidiert.
- `scope_lock`: `id` (PK), `resource_id` (FK), `locked_by`, `reason`, `blocks_read` (bool, Default `false`), `expires_at` (nullable), `created_at`, `released_at`/`released_by` (nullable) — nie hart gelöscht, die Aufhebung wird dokumentiert statt die Zeile zu entfernen (Audit-Trail bleibt vollständig).

## Bereichssperren (4.7, seit P3-S4)

- Eine Bereichssperre gilt **immer für den gesamten Unterbaum** ab `resource_id` — unabhängig vom `inherit`-Flag des jeweiligen Knotens, das ausschließlich die RBAC-Vererbung steuert (siehe `repository.get_active_scope_locks_for_resource`, läuft dieselbe Vorfahrenkette ab wie die Rechte-Auswertung, aber ohne am `inherit=false`-Knoten abzubrechen).
- `blocks_read=false` (Default) blockiert nur Schreibzugriffe, `blocks_read=true` zusätzlich Lesezugriffe. `GET /check` erwartet dafür einen expliziten `access_type`-Parameter (`read`|`write`, Default `write`) — der aufrufende Dienst muss angeben, welche Art von Zugriff geprüft wird.
- **Überlagert RBAC statt es zu verändern**: Eine aktive, blockierende Sperre führt zu `allowed=false` unabhängig von den eigentlich zugewiesenen Rechten. Ausnahme: Principals mit der Capability `scope_lock.bypass` (per normaler Rollenzuweisung vergeben) umgehen die Sperre — nach Aufhebung gelten sofort wieder die ursprünglichen Rechte, ohne dass irgendetwas manuell entzogen/neu vergeben werden musste.
- **Klare Rückmeldung statt generischem Fehler**: `CheckResult` liefert bei einer blockierenden Sperre zusätzlich `blocked_by_scope_lock`, `scope_lock_reason` und `scope_lock_expires_at`, damit aufrufende Dienste (künftig API-Gateway/UI) Grund und voraussichtliche Dauer anzeigen können statt eines unspezifischen "keine Berechtigung".
- **Wer Sperren setzen/aufheben darf, ist weiterhin nicht durchgesetzt**: Die Endpunkte selbst sind ungated (analog zum Force-Unlock-Präzedenzfall im Document Service, P3-S2). Das seit P4-S1 existierende API-Gateway (3.5) prüft nur, dass überhaupt ein gültiger Bearer-Token vorliegt, nicht, ob der Principal zum Sperren berechtigt ist — reale Autorisierung ("nur Admin-Rollen dürfen sperren") bräuchte eine Auswertung der vom Gateway weitergereichten Identitäts-Header in diesem Service selbst, ebenso ein optionales Vier-Augen-Prinzip (4.3) für das Setzen/Aufheben.
- **Auditierung**: `POST /scope-locks` und `DELETE /scope-locks/{id}` publizieren `permission.scope_lock.created`/`.released` über einen eigenen Producer-Client (getrennt vom reinen Struktur-Konsumenten, siehe unten) — der Audit Service konsumiert seit dieser Session zusätzlich `permission.>`.

## Vererbungsalgorithmus

Von der angefragten Ressource aus wird die Vorfahrenkette (`parent_id`) nach oben durchlaufen, an jedem Knoten werden die Zuweisungen des Principals gesammelt. Ein Knoten mit `inherit=false` beendet den Aufstieg **nach** Auswertung seiner eigenen Zuweisungen — Standard-DMS-Verhalten (SharePoint/Alfresco), wie in Konzept 4.1 gefordert.

## Struktur-Synchronisation (Vertrag bestätigt seit P3-S3)

Der Folder Service (P3-S3) implementiert genau den in P2-S2 provisorisch angenommenen Vertrag — keine Anpassung nötig. `structure_consumer.py` abonniert `settings.structure_subjects` (Default `["folder.>"]`) über `NatsEventBusClient(ensure_stream=False)`:

| event_type (Suffix) | payload |
|---|---|
| `*.resource.created` | `{resource_id, parent_id, resource_type}` |
| `*.resource.moved` | `{resource_id, new_parent_id}` |
| `*.resource.deleted` | `{resource_id}` |

Live end-to-end verifiziert (P3-S3): ein über die echte Folder-Service-API angelegter Ordner erscheint unmittelbar als `ResourceNode` in diesem Service, inklusive korrektem `parent_id`.

**Bekannte Grenze**: Existiert beim Start noch kein Stream für ein konfiguriertes Subject (kein Producer je gelaufen), wird das Subject übersprungen (`SubjectNotFoundError` abgefangen, siehe `dms-eventbus-client`/ADR 0001) statt den Service-Start zu blockieren — es gibt aber keinen Retry-Loop, der den Stream später automatisch nachzieht; ein Neustart ist dann nötig. In der Praxis unkritisch, da der Folder Service inzwischen existiert und seinen Stream beim eigenen Start anlegt.

## Events

**Konsumiert:** `folder.>` (Vertrag bestätigt, s. o.).
**Publiziert** (Stream `permission`, `ensure_stream=True`, seit P3-S4):

| event_type | payload |
|---|---|
| `permission.scope_lock.created` | `{scope_lock_id, resource_id, locked_by, reason, blocks_read}` |
| `permission.scope_lock.released` | `{scope_lock_id, resource_id, released_by}` |

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Gruppenmitgliedschaft wird nicht aufgelöst: `principal_id` einer Zuweisung muss exakt dem abgefragten Principal entsprechen. Eine Auflösung "Nutzer X ist Mitglied von Gruppe Y, die Rolle Z hat" ist nicht Teil dieser Session (hängt von AD-Gruppen-Sync im Auth Service, 4.4, ab, der ebenfalls noch offen ist).
- Granularere Cache-Invalidierung (nur betroffener Teilbaum statt gesamter Cache) als spätere Optimierung möglich, ohne die API zu ändern.
- Konfigurierbares Vier-Augen-Prinzip (4.3) für Rechteänderungen ist nicht Teil dieser Session.
- Keine Durchsetzung, wer Bereichssperren setzen/aufheben darf: Das Gateway (3.5, P4-S1) prüft seit dieser Session zentral, dass ein Aufrufer *irgendeinen* gültigen Bearer-Token hat, aber keine Autorisierung für die konkrete Aktion (`POST`/`DELETE /scope-locks`) — jeder authentifizierte Principal kann aktuell Sperren setzen/aufheben. Eine echte Capability-Prüfung bräuchte, dass der Permission Service die vom Gateway weitergereichten Identitäts-Header auswertet (siehe `docs/services/gateway-service.md`) — analog zum bereits bestehenden offenen Punkt beim Document-Service-Force-Unlock.
- `GET /check` verlässt sich auf den Aufrufer, den korrekten `access_type` (`read`/`write`) mitzugeben — der Service selbst kennt keine feste Zuordnung Permission-Name → Zugriffsart.
