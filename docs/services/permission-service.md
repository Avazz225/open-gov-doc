# permission-service

**Verantwortung:** RBAC — Rollen, Zuweisungen an Principals (Nutzer/Gruppen) an Ressourcen, Vererbung entlang einer Ressourcen-Hierarchie, materialisierter ereignisgetriebener Rechte-Cache (Konzept 4.1). Seit P3-S4 zusätzlich Bereichssperren (4.7): temporäre, RBAC-überlagernde Sperrung ganzer Ressourcen-Teilbäume. Seit P6-S4 zusätzlich der generische Vier-Augen-Approval-Mechanismus (4.3), den auch andere Services (z. B. Document Service) nutzen. Seit P6-S5 zusätzlich Heimat der systemeigenen, domänengetrennten Admin-Rollen (4.6) — von Keycloak-Realm-Rollen komplett getrennt, siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md).

**Konzept-Referenz:** 4.1, 4.3, 4.6, 4.7
**Eigenes Postgres-Schema:** `permission` (Tabellen `role`, `role_assignment`, `resource_node`, `effective_permission_cache`, `scope_lock`, `approval_action_config`, `approval_request`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/roles` | Rolle anlegen |
| `GET` | `/roles` | Alle Rollen |
| `POST` | `/role-assignments` | Zuweisung anlegen (404 bei unbekannter Rolle/Ressource) |
| `GET` | `/role-assignments?principal_id=...&resource_id=...` | Zuweisungen auflisten, optional gefiltert (seit P4-S3, Grundlage der Admin-UI) |
| `DELETE` | `/role-assignments/{id}` | Zuweisung entfernen |
| `GET` | `/resources/{id}` | Ressourcenknoten lesen |
| `PATCH` | `/resources/{id}` | `inherit` umschalten |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Gecachte effektive Rollen/Rechte |
| `GET` | `/check?...&access_type=read\|write` | Autorisierungs-Check inkl. Bereichssperren-Überlagerung |
| `POST` | `/check/batch` | Batch-Form von `/check` (seit P5-S4, Search Service) — mehrere `resource_ids` in einem Aufruf |
| `POST` | `/scope-locks` | Bereichssperre setzen — Antwort `{status: "created"\|"pending_approval", scope_lock, approval_request_id}` (seit P6-S4, s. u.), `404` bei unbekannter Ressource |
| `DELETE` | `/scope-locks/{id}` | Bereichssperre aufheben (`released_by`) — gleicher Antwort-Vertrag (`status: "released"\|"pending_approval"`) |
| `GET` | `/scope-locks?resource_id=` | Sperren auflisten (optional gefiltert) |
| `GET` | `/scope-locks/effective/{resource_id}` | Aktive Sperren, die diese Ressource betreffen (inkl. geerbte) |
| `GET` | `/approval-config` | Alle konfigurierten Aktionstypen (4.3, seit P6-S4) |
| `GET` | `/approval-config/{action_type}` | Konfiguration eines Aktionstyps — Default `requires_approval=false`, falls nie gesetzt |
| `PUT` | `/approval-config/{action_type}` | Genehmigungspflicht für einen Aktionstyp setzen (Upsert) |
| `POST` | `/approval-requests` | Freigabe-Request anlegen (`action_type`, `initiated_by`, `payload`) — `403`, falls der Aktionstyp eine `required_permission` (seit P6-S5) verlangt, die `initiated_by` nicht hält |
| `GET` | `/approval-requests?status=&action_type=` | Requests auflisten, optional gefiltert |
| `GET` | `/approval-requests/{id}` | Request-Detail (`404`) |
| `POST` | `/approval-requests/{id}/approve` | Genehmigen (`approved_by`) — `403` falls identisch mit `initiated_by` **oder** falls die für den Aktionstyp konfigurierte `required_permission` fehlt (seit P6-S5), `409` falls bereits entschieden |
| `POST` | `/approval-requests/{id}/reject` | Ablehnen (`rejected_by`, optional `reason`) — `409` falls bereits entschieden |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `resource_node`: `resource_id` (PK), `parent_id` (self-FK, nullable), `resource_type`, `inherit` (bool). Wurzelknoten `"root"` wird beim Start idempotent angelegt.
- `role`: `id`, `name` (unique), `description`, `permissions` (JSON-Liste von Capability-Strings, z. B. `["read","write"]`).
- `role_assignment`: `principal_type` (`user`|`group`), `principal_id`, `role_id`, `resource_id` — unique auf der Kombination.
- `effective_permission_cache`: `(principal_id, resource_id)` → `roles`, `permissions`, `computed_at`. Wird bei jeder Rechte-/Strukturänderung **vollständig geleert** (bewusste Vereinfachung, siehe README) statt granular je Teilbaum invalidiert.
- `scope_lock`: `id` (PK), `resource_id` (FK), `locked_by`, `reason`, `blocks_read` (bool, Default `false`), `expires_at` (nullable), `created_at`, `released_at`/`released_by` (nullable) — nie hart gelöscht, die Aufhebung wird dokumentiert statt die Zeile zu entfernen (Audit-Trail bleibt vollständig).
- `approval_action_config` (4.3, seit P6-S4): `action_type` (PK, freier String), `requires_approval` (bool, Default `false`), `required_permission` (nullable String, seit **P6-S5**, 4.6), `updated_at`. Fehlt eine Zeile für einen Aktionstyp, gilt implizit `requires_approval=false`/`required_permission=null` (transientes Default-Objekt, nicht persistiert).
- `approval_request` (4.3, seit P6-S4): `id` (UUID-str), `action_type`, `initiated_by`, `payload` (JSON — genug Information, um die Aktion später auszuführen), `status` (`pending`\|`approved`\|`rejected`), `approved_by`/`rejected_by`/`reason` (nullable), `created_at`, `decided_at` (nullable).

## Bereichssperren (4.7, seit P3-S4)

- Eine Bereichssperre gilt **immer für den gesamten Unterbaum** ab `resource_id` — unabhängig vom `inherit`-Flag des jeweiligen Knotens, das ausschließlich die RBAC-Vererbung steuert (siehe `repository.get_active_scope_locks_for_resource`, läuft dieselbe Vorfahrenkette ab wie die Rechte-Auswertung, aber ohne am `inherit=false`-Knoten abzubrechen).
- `blocks_read=false` (Default) blockiert nur Schreibzugriffe, `blocks_read=true` zusätzlich Lesezugriffe. `GET /check` erwartet dafür einen expliziten `access_type`-Parameter (`read`|`write`, Default `write`) — der aufrufende Dienst muss angeben, welche Art von Zugriff geprüft wird.
- **Überlagert RBAC statt es zu verändern**: Eine aktive, blockierende Sperre führt zu `allowed=false` unabhängig von den eigentlich zugewiesenen Rechten. Ausnahme: Principals mit der Capability `scope_lock.bypass` (per normaler Rollenzuweisung vergeben) umgehen die Sperre — nach Aufhebung gelten sofort wieder die ursprünglichen Rechte, ohne dass irgendetwas manuell entzogen/neu vergeben werden musste.
- **Klare Rückmeldung statt generischem Fehler**: `CheckResult` liefert bei einer blockierenden Sperre zusätzlich `blocked_by_scope_lock`, `scope_lock_reason` und `scope_lock_expires_at`, damit aufrufende Dienste (künftig API-Gateway/UI) Grund und voraussichtliche Dauer anzeigen können statt eines unspezifischen "keine Berechtigung".
- **Wer Sperren setzen/aufheben darf, ist weiterhin nicht durchgesetzt**: Die Endpunkte selbst sind ungated (analog zum Force-Unlock-Präzedenzfall im Document Service, P3-S2). Das seit P4-S1 existierende API-Gateway (3.5) prüft nur, dass überhaupt ein gültiger Bearer-Token vorliegt, nicht, ob der Principal zum Sperren berechtigt ist — reale Autorisierung ("nur Admin-Rollen dürfen sperren") bräuchte eine Auswertung der vom Gateway weitergereichten Identitäts-Header in diesem Service selbst. Seit **P6-S4** kann optional ein Vier-Augen-Prinzip aktiviert werden (siehe unten) — das ersetzt keine Rollenprüfung, sondern ergänzt eine zweite Person im Freigabe-Fluss.
- **Auditierung**: `POST /scope-locks` und `DELETE /scope-locks/{id}` publizieren bei sofortiger Ausführung `permission.scope_lock.created`/`.released` über einen eigenen Producer-Client (getrennt vom reinen Struktur-Konsumenten, siehe unten) — der Audit Service konsumiert seit P3-S4 zusätzlich `permission.>`. Bei genehmigungspflichtiger Ausführung (s. u.) werden dieselben Events erst nach Genehmigung publiziert, vom `approval_consumer.py`.

## Vier-Augen-Approval-Mechanismus (4.3, seit P6-S4)

Generischer, pro Aktionstyp konfigurierbarer Freigabe-Mechanismus — siehe [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) für die vollständige Architekturentscheidung. Kurzfassung:

- **Konfiguration pro Aktionstyp** (`approval_action_config`): ohne explizite `PUT /approval-config/{action_type} {"requires_approval": true}` bleibt jede Aktion ungated (Default `false`) — "konfigurierbar pro Aktionstyp, nicht global erzwungen" wörtlich umgesetzt.
- **Ablauf bei aktivierter Genehmigungspflicht**: der gegatete Endpunkt (hier: `POST`/`DELETE /scope-locks`, extern: `document-service`s Force-Unlock) legt statt direkter Ausführung einen `ApprovalRequest` an (`status="pending"`) und publiziert `permission.approval.requested`. Eine zweite Person ruft `POST /approval-requests/{id}/approve` auf (`403`, falls `approved_by == initiated_by`) — dies publiziert `permission.approval.approved` mit dem ursprünglichen `payload`. **Die eigentliche Aktion wird nicht hier ausgeführt**, sondern von einem Konsumenten dieses Events.
- **Selbst-Konsum für eigene Aktionstypen**: `permission-service` konsumiert `permission.approval.approved` selbst (`approval_consumer.py`) für `permission.scope_lock.create`/`.release` — exakt derselbe Mechanismus wie für einen fremden Service, keine Sonderbehandlung im `approve`-Handler. `document-service` konsumiert dasselbe Event für `document.force_unlock` (sein erster Konsument überhaupt, siehe `docs/services/document-service.md`).
- **Kein Ausführungs-Rückkanal**: ein extern ausgeführter, aber fehlgeschlagener Vollzug (z. B. Sperre inzwischen anderweitig aufgehoben) bleibt beim ausführenden Service geloggt, `ApprovalRequest.status` bleibt bei `"approved"` — siehe ADR 0022 "Konsequenzen".
- **Nicht Teil dieser Session**: Rechte-/Rollenänderungen (`POST /role-assignments` u. Ä.) sind ein weiteres, in 4.3 genanntes Beispiel für einen gate-fähigen Aktionstyp, aber weiterhin nicht angebunden — der Mechanismus selbst ist generisch nutzbar, müsste dafür aber am jeweiligen Endpunkt genauso verdrahtet werden wie hier bei den Scope-Locks.
- **`required_permission` (4.6, seit P6-S5)**: generische Erweiterung von `ApprovalActionConfig` — ist gesetzt, müssen sowohl `initiated_by` als auch `approved_by` diese Capability laut `GET /effective-permissions/.../root` halten (zusätzlich zur Initiator≠Genehmiger-Regel), sonst `403` (`MissingRequiredPermissionError`). Wird für `auth.superuser.activate` beim Start fest auf `breakglass.approve` gesetzt (Superuser Break-Glass, siehe unten und `docs/services/auth-service.md`) — strengere Umsetzung von "zwei verschiedene Mitglieder einer Berechtigungsgruppe" (4.6) als das bloße "irgendeine zweite Person" aus 4.3. Bleibt für Scope-Locks/Force-Unlock `null`, unverändertes Verhalten.

## Domänengetrennte Admin-Rollen (4.6, seit P6-S5)

Systemeigen (nicht Keycloak-Realm-Rollen) — vollständige Architekturbegründung in [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). `repository.ensure_domain_admin_roles` seedet bei jedem Start idempotent 8 `Role`-Zeilen (falls nicht bereits vorhanden, per Name geprüft):

| Rolle | Capability | Zugeordnetes technisches Konto |
|---|---|---|
| `domain-admin-users` | `admin.user_management` | `users-admin` (seit P6-S5 angelegt, `auth-service`) |
| `domain-admin-config` | `admin.object_config` | noch keins |
| `domain-admin-storage` | `admin.storage` | noch keins |
| `domain-admin-license` | `admin.license` | noch keins |
| `domain-admin-query-console` | `admin.query_console` | noch keins |
| `domain-admin-deletion` | `admin.deletion` | noch keins |
| `domain-admin-deletion-vs` | `admin.deletion_classified` | noch keins |
| `breakglass-approver` | `breakglass.approve` | keins (echte Menschen, manuell zugewiesen) |

Nur `domain-admin-users` ist diese Session tatsächlich durchgesetzt (`auth-service`s `/users`, Admin-UI-Gating) — die übrigen sind vordefiniert ("standardmäßig mitgeliefert", 4.6), aber ohne Konto/Enforcement, da die zugehörigen fachlichen Funktionen teils noch nicht existieren (Lizenzverwaltung, Query-Konsole). `breakglass-approver` bekommt bewusst kein automatisches Konto — die Vier-Augen-Regel aus 4.6 verlangt zwei *verschiedene, echte* Personen, keine geteilte Technik-Identität; Zuweisung an konkrete Menschen läuft über die bestehende, jetzt selbst gegatete `POST /role-assignments`-Nutzung in der Admin-UI.

## Batch-Check (seit P5-S4, Search Service)

`POST /check/batch` (Body: `{principal_id, permission, access_type, resource_ids: [...]}`, Antwort: `{results: {resource_id: bool}}`) wurde für den neuen Search Service ergänzt: eine Suchergebnisliste kann viele verschiedene Ordner betreffen, und der bestehende `GET /check` prüft nur ein Principal/Resource/Permission-Tripel je Aufruf — viele Einzel-Roundtrips wären für eine Ergebnisseite unpraktikabel. Die Implementierung wiederholt exakt dieselbe Logik wie `/check` (inkl. Bereichssperren-Überlagerung) in einer Schleife über die (deduplizierten) `resource_ids` — jeder Durchlauf trifft den bereits vorhandenen `effective_permission_cache`, daher keine teure Mehrfachberechnung trotz der Schleife; eine SQL-seitige Mega-Query wäre für den hier relevanten Umfang (Suchergebnisseiten, keine Massenoperationen) Overengineering. Search Service nutzt Ordner-`resource_id`s (nicht Dokument-IDs) als Prüfobjekt — Dokumente sind selbst keine Permission-Resources, siehe `docs/services/search-service.md`.

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

**Konsumiert:** `folder.>` (Vertrag bestätigt, s. o.); seit P6-S4 zusätzlich sein eigenes `permission.approval.approved` (Selbst-Konsum, siehe oben).
**Publiziert** (Stream `permission`, `ensure_stream=True`, seit P3-S4):

| event_type | payload |
|---|---|
| `permission.scope_lock.created` | `{scope_lock_id, resource_id, locked_by, reason, blocks_read}` |
| `permission.scope_lock.released` | `{scope_lock_id, resource_id, released_by}` |
| `permission.approval.requested` | `{request_id, action_type, initiated_by}` (seit P6-S4) |
| `permission.approval.approved` | `{request_id, action_type, initiated_by, approved_by, payload}` (seit P6-S4) |
| `permission.approval.rejected` | `{request_id, action_type, initiated_by, rejected_by, reason}` (seit P6-S4) |

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Gruppenmitgliedschaft wird nicht aufgelöst: `principal_id` einer Zuweisung muss exakt dem abgefragten Principal entsprechen. Eine Auflösung "Nutzer X ist Mitglied von Gruppe Y, die Rolle Z hat" ist nicht Teil dieser Session (hängt von AD-Gruppen-Sync im Auth Service, 4.4, ab, der ebenfalls noch offen ist).
- Granularere Cache-Invalidierung (nur betroffener Teilbaum statt gesamter Cache) als spätere Optimierung möglich, ohne die API zu ändern.
- **Vier-Augen-Prinzip (4.3) ist seit P6-S4 generisch verfügbar, seit P6-S5 auch mit optionaler Rollenbindung (`required_permission`)** — verdrahtet für Bereichssperren, Document-Service-Force-Unlock und Superuser-Break-Glass (`auth.superuser.activate`). Rechte-/Rollenänderungen (`POST /role-assignments`, `POST /roles`) nutzen ihn weiterhin nicht, siehe "Vier-Augen-Approval-Mechanismus" oben.
- Keine Durchsetzung, wer Bereichssperren setzen/aufheben darf: Das Gateway (3.5, P4-S1) prüft nur, dass ein Aufrufer *irgendeinen* gültigen Bearer-Token hat, aber keine Autorisierung für die konkrete Aktion (`POST`/`DELETE /scope-locks`) — jeder authentifizierte Principal kann aktuell Sperren setzen/aufheben (optional mit Vier-Augen-Zwischenschritt, s. o., aber ohne Rollenprüfung, wer überhaupt initiieren/genehmigen darf). Eine echte Capability-Prüfung bräuchte, dass der Permission Service die vom Gateway weitergereichten Identitäts-Header auswertet (siehe `docs/services/gateway-service.md`) — analog zum bereits bestehenden offenen Punkt beim Document-Service-Force-Unlock.
- **Kein Ausführungs-Rückkanal / keine Genehmigenden-Benachrichtigung** für den Approval-Mechanismus — siehe [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) "Konsequenzen".
- `GET /check` verlässt sich auf den Aufrufer, den korrekten `access_type` (`read`/`write`) mitzugeben — der Service selbst kennt keine feste Zuordnung Permission-Name → Zugriffsart.
- **`POST`/`GET /roles` und `POST`/`GET`/`DELETE /role-assignments` bleiben ungated** (seit P6-S5 bewusst nicht angefasst): der Retrofit-Auftrag benannte explizit nur Auth-Service `/users` + Admin-UI, nicht diese Endpunkte selbst — zusätzlich hätte ein Gate hier den Bootstrap-Seeding-Aufruf von `auth-service` (der die allererste Rollenzuweisung anlegt) vor ein Henne-Ei-Problem gestellt, siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md).
- **6 der 7 Domain-Admin-Rollen aus 4.6 ohne zugeordnetes technisches Konto** (seit P6-S5): siehe "Domänengetrennte Admin-Rollen" oben — folgt jeweils mit der künftigen Retrofit-Session der betreffenden Domäne.
