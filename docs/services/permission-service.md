# permission-service

**Verantwortung:** RBAC — Rollen, Zuweisungen an Principals (Nutzer/Gruppen) an Ressourcen, Vererbung entlang einer Ressourcen-Hierarchie, materialisierter ereignisgetriebener Rechte-Cache (Konzept 4.1). Seit P3-S4 zusätzlich Bereichssperren (4.7): temporäre, RBAC-überlagernde Sperrung ganzer Ressourcen-Teilbäume. Seit P6-S4 zusätzlich der generische Vier-Augen-Approval-Mechanismus (4.3), den auch andere Services (z. B. Document Service) nutzen. Seit P6-S5 zusätzlich Heimat der systemeigenen, domänengetrennten Admin-Rollen (4.6) — von Keycloak-Realm-Rollen komplett getrennt, siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Seit P6-S6 zusätzlich Heimat des systemweiten Wartungsmodus-Zustands (Not-Shutdown, 4.8) — siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Konzept-Referenz:** 4.1, 4.3, 4.6, 4.7, 4.8, 4.4a (Stellvertretung bei Abwesenheit, seit P14-S11)
**Eigenes Postgres-Schema:** `permission` (Tabellen `role`, `role_assignment`, `resource_node`, `effective_permission_cache`, `scope_lock`, `approval_action_config`, `approval_request`, `system_maintenance_mode`, `delegation`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/roles` | Rolle anlegen — seit P19-S6 gegated: `X-DMS-Principal` muss `admin.user_management` halten ([ADR 0071](../adr/0071-permission-service-self-gating.md)), sonst `401`/`403` |
| `GET` | `/roles` | Alle Rollen — bewusst weiterhin ungegatet, siehe ADR 0071 "Begründung" |
| `PUT` | `/roles/{role_id}` | Beschreibung/Berechtigungen aktualisieren (`name` unveränderlich) — seit P12-S3, Grundlage für `config-service`s Rollen-Upsert per Name (7.3); seit P19-S6 ebenfalls `admin.user_management`-gegated |
| `POST` | `/role-assignments` | Zuweisung anlegen — Antwort `{status: "created"\|"pending_approval", role_assignment, approval_request_id}` (seit P17-S3, `permission.role_assignment.create`, s. u.), `404` bei unbekannter Rolle/Ressource |
| `GET` | `/role-assignments?principal_id=...&resource_id=...` | Zuweisungen auflisten, optional gefiltert (seit P4-S3, Grundlage der Admin-UI) |
| `DELETE` | `/role-assignments/{id}` | Zuweisung entfernen |
| `GET` | `/resources/{id}` | Ressourcenknoten lesen |
| `PATCH` | `/resources/{id}` | `inherit` umschalten |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Gecachte effektive Rollen/Rechte |
| `GET` | `/check?...&access_type=read\|write` | Autorisierungs-Check inkl. Bereichssperren-Überlagerung |
| `POST` | `/check/batch` | Batch-Form von `/check` (seit P5-S4, Search Service) — mehrere `resource_ids` in einem Aufruf |
| `POST` | `/scope-locks` | Bereichssperre setzen — Antwort `{status: "created"\|"pending_approval", scope_lock, approval_request_id}` (seit P6-S4, s. u.), `404` bei unbekannter Ressource. Seit P19-S6 gegated: `locked_by` muss `admin.user_management` halten (ADR 0071), sonst `403` — Prüfung läuft vor dem Vier-Augen-Zweig |
| `DELETE` | `/scope-locks/{id}` | Bereichssperre aufheben (`released_by`) — gleicher Antwort-Vertrag (`status: "released"\|"pending_approval"`); seit P19-S6 ebenso `admin.user_management`-gegated über `released_by` |
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
| `GET` | `/maintenance-mode` | Wartungsmodus-Status (4.8, seit P6-S6): `{active, reason, triggered_by, activated_at, lifted_by, lifted_at}` |
| `POST` | `/maintenance-mode/trigger` | Auslösen (`triggered_by`, optional `reason`) — prüft immer zuerst die Capability `system.not_shutdown.trigger` bei `triggered_by` (`403` sonst), dann direkte Aktivierung oder Vier-Augen-Freigabe je nach `approval-config`; Antwort `{status: "activated"\|"pending_approval", maintenance_mode, approval_request_id}` |
| `POST` | `/maintenance-mode/lift` | Aufheben (`lifted_by`) — `403`, falls `lifted_by` nicht dem aktuell aktiven Superuser entspricht (Cross-Service-Check gegen `auth-service`, s. u.) |
| `GET` | `/healthz` | Health-Check |
| `POST` | `/delegations` | Stellvertretung hinterlegen (4.4a, seit P14-S11) — `delegator_principal_id` ist immer `X-DMS-Principal` (`401` ohne Header), `400` bei `ends_at <= starts_at`. Publiziert `permission.delegation.created` |
| `GET` | `/delegations?delegator_principal_id=&deputy_principal_id=&active_only=` | Delegationen auflisten, optional gefiltert |
| `GET` | `/delegations/active-for-deputy/{principal_id}` | Für wen `principal_id` gerade aktiv als Stellvertretung eingetragen ist — Grundlage der "Im Auftrag von"-Auswahl in `reviewer-ui`/`user-ui` |
| `GET` | `/delegations/check?deputy_principal_id=&delegator_principal_id=&process_definition_id=&object_type_id=&folder_resource_id=` | Eigentlicher Durchsetzungs-Endpunkt (gleiches Antwortformat wie `/check`) — von `workflow-service` bei Aufgabenabschluss "im Auftrag von" aufgerufen |
| `DELETE` | `/delegations/{id}` | Vorzeitiger Widerruf — nur die vertretene Person oder `X-DMS-Roles: dms-admin` (konfigurierbar, `delegation_revoke_admin_role`), `404` bei unbekannter ID, idempotent. Publiziert `permission.delegation.revoked` |

## Datenmodell

- `resource_node`: `resource_id` (PK), `parent_id` (self-FK, nullable), `resource_type`, `inherit` (bool). Wurzelknoten `"root"` wird beim Start idempotent angelegt.
- `role`: `id`, `name` (unique), `description`, `permissions` (JSON-Liste von Capability-Strings, z. B. `["read","write"]`).
- `role_assignment`: `principal_type` (`user`|`group`), `principal_id`, `role_id`, `resource_id` — unique auf der Kombination. `principal_type="group"` war bis Phase 19 Session 2 reine Schema-Deko (nirgends ausgewertet) — seit dieser Session wird genau ein reservierter Wert (`principal_id="everyone"`) tatsächlich verarbeitet, siehe "'everyone'-Gruppe" unten.
- `effective_permission_cache`: `(principal_id, resource_id)` → `roles`, `permissions`, `computed_at`. Wird bei jeder Rechte-/Strukturänderung **vollständig geleert** (bewusste Vereinfachung, siehe README) statt granular je Teilbaum invalidiert.
- `scope_lock`: `id` (PK), `resource_id` (FK), `locked_by`, `reason`, `blocks_read` (bool, Default `false`), `expires_at` (nullable), `created_at`, `released_at`/`released_by` (nullable) — nie hart gelöscht, die Aufhebung wird dokumentiert statt die Zeile zu entfernen (Audit-Trail bleibt vollständig).
- `approval_action_config` (4.3, seit P6-S4): `action_type` (PK, freier String), `requires_approval` (bool, Default `false`), `required_permission` (nullable String, seit **P6-S5**, 4.6), `updated_at`. Fehlt eine Zeile für einen Aktionstyp, gilt implizit `requires_approval=false`/`required_permission=null` (transientes Default-Objekt, nicht persistiert).
- `approval_request` (4.3, seit P6-S4): `id` (UUID-str), `action_type`, `initiated_by`, `payload` (JSON — genug Information, um die Aktion später auszuführen), `status` (`pending`\|`approved`\|`rejected`), `approved_by`/`rejected_by`/`reason` (nullable), `created_at`, `decided_at` (nullable).
- `system_maintenance_mode` (4.8, seit P6-S6): Singleton (`id=1`, fest, gleiches Muster wie `OcrConfig`/`GuardConfig`), `active` (bool), `reason` (nullable), `triggered_by` (nullable), `activated_at` (nullable), `lifted_by`/`lifted_at` (nullable) — bei erneuter Aktivierung nach einer Aufhebung werden `lifted_by`/`lifted_at` zurückgesetzt.
- `delegation` (4.4a, seit P14-S11): `id` (UUID-str, PK), `delegator_principal_id`/`deputy_principal_id`, `starts_at`/`ends_at` (beide Pflicht), `scope_object_type_ids`/`scope_process_definition_ids`/`scope_folder_resource_ids` (je JSON-Liste, `null` = auf dieser Dimension uneingeschränkt), `created_at`, `revoked_at`/`revoked_by` (nullable) — nie hart gelöscht, gleiches Muster wie `scope_lock` oben.

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
- **Selbst-Konsum für eigene Aktionstypen**: `permission-service` konsumiert `permission.approval.approved` selbst (`approval_consumer.py`) für `permission.scope_lock.create`/`.release` — exakt derselbe Mechanismus wie für einen fremden Service, keine Sonderbehandlung im `approve`-Handler. `document-service` konsumiert dasselbe Event für `document.force_unlock` (sein erster Konsument überhaupt, siehe `docs/services/document-service.md`). Seit **P8-S2** konsumiert auch `query-service` dasselbe Event für seine drei Manipulations-Aktionstypen (`document.attribute_reset`, `permission.role_assignment.delete`, `object_type.update`), siehe `docs/services/query-service.md`.
- **Auch über das CLI-Tool nutzbar** (seit P8-S3, 6.2) — `dms role list/assignment ...` (`/roles`, `/role-assignments`) und `dms query approvals list/approve` (`/approval-requests`) sprechen dieselben Endpunkte wie Admin-UI/Web-Clients, siehe `docs/tools/cli.md`.
- **Kein Ausführungs-Rückkanal**: ein extern ausgeführter, aber fehlgeschlagener Vollzug (z. B. Sperre inzwischen anderweitig aufgehoben) bleibt beim ausführenden Service geloggt, `ApprovalRequest.status` bleibt bei `"approved"` — siehe ADR 0022 "Konsequenzen".
- **Rechte-/Rollenänderungen seit P17-S3 angebunden**: `POST /role-assignments` prüft `permission.role_assignment.create` über denselben Mechanismus (14.2 "Berechtigungsänderung", eines der drei vom eGov-Paket standardmäßig gegateten Aktionstypen, siehe `packages/egov/`) — Antwort `RoleAssignmentActionResult` (`status`/`role_assignment`/`approval_request_id`), gleiches Envelope-Muster wie bei Scope-Locks. `initiated_by` ist mangels eigenem Antragsteller-Feld auf `RoleAssignmentCreate` die `principal_id` (die Person, die die Rolle erhalten soll) — identischer Kompromiss wie bei den Scope-Locks. Genehmigte Zuweisungen werden im selben Selbst-Konsum-Zweig von `approval_consumer.py` nachvollzogen wie Bereichssperren/Notfallsperre.
- **`required_permission` (4.6, seit P6-S5)**: generische Erweiterung von `ApprovalActionConfig` — ist gesetzt, müssen sowohl `initiated_by` als auch `approved_by` diese Capability laut `GET /effective-permissions/.../root` halten (zusätzlich zur Initiator≠Genehmiger-Regel), sonst `403` (`MissingRequiredPermissionError`). Wird für `auth.superuser.activate` beim Start fest auf `breakglass.approve` gesetzt (Superuser Break-Glass, siehe unten und `docs/services/auth-service.md`) — strengere Umsetzung von "zwei verschiedene Mitglieder einer Berechtigungsgruppe" (4.6) als das bloße "irgendeine zweite Person" aus 4.3. Bleibt für Scope-Locks/Force-Unlock `null`, unverändertes Verhalten.

## Domänengetrennte Admin-Rollen (4.6, seit P6-S5)

Systemeigen (nicht Keycloak-Realm-Rollen) — vollständige Architekturbegründung in [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). `repository.ensure_domain_admin_roles` seedet bei jedem Start idempotent 9 `Role`-Zeilen (falls nicht bereits vorhanden, per Name geprüft):

| Rolle | Capability | Zugeordnetes technisches Konto |
|---|---|---|
| `domain-admin-users` | `admin.user_management` | `users-admin` (seit P6-S5 angelegt, `auth-service`) |
| `domain-admin-config` | `admin.object_config` | `config-admin` (seit **P6-S6** angelegt, `auth-service`) |
| `domain-admin-storage` | `admin.storage` | noch keins |
| `domain-admin-license` | `admin.license` | keins (Durchsetzung direkt in `license-service`, seit **P9-S1**, siehe unten) |
| `domain-admin-query-console` | `admin.query_console` | keins (Durchsetzung direkt in `query-service`, seit **P8-S1**, siehe unten) |
| `domain-admin-query-console-manipulate` | `admin.query_console.manipulate` | keins (Durchsetzung direkt in `query-service`, seit **P8-S2**) |
| `domain-admin-deletion` | `admin.deletion` | noch keins |
| `domain-admin-deletion-vs` | `admin.deletion_classified` | noch keins |
| `breakglass-approver` | `breakglass.approve` | keins (echte Menschen, manuell zugewiesen) |
| `domain-admin-emergency` | `system.not_shutdown.trigger` | keins (seit **P6-S6**, echte Menschen, manuell zugewiesen — siehe "Not-Shutdown" unten) |
| `domain-admin-virus-scan` | `admin.quarantine` | keins (seit **Post-Roadmap Phase 19 Session 8**, [ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md) — Durchsetzung direkt in `virus-scan-service`, ersetzt dessen vorheriges reines `X-DMS-Roles`-Gate) |
| `domain-admin-legal-hold` | `admin.legal_hold` | keins (seit **Post-Roadmap Phase 19 Session 10**, [ADR 0075](../adr/0075-legal-hold-rbac.md) — Durchsetzung in `document-service`/`folder-service`, `user-ui`s `RetentionPanel`/`FolderRetentionModal` blenden den Aktionsbutton entsprechend ein) |

`domain-admin-users` und (seit **P6-S6**) `domain-admin-config` haben ein eigenes technisches Keycloak-Konto (`auth-service`s `/users` bzw. `workflow-service`s Prozessdefinitions-Endpunkte, Admin-UI-Gating). `domain-admin-query-console`/`-manipulate` (seit **P8-S1**/**P8-S2**) sowie **seit P9-S1** `domain-admin-license` sind ebenfalls tatsächlich durchgesetzt, aber **ohne** eigenes technisches Konto — der jeweilige Service prüft die Rollenzuweisung direkt über `GET /effective-permissions/{principal}/root`, kein dediziertes Konto nötig (siehe `docs/services/query-service.md`/`docs/services/license-service.md`). Die übrigen sind vordefiniert ("standardmäßig mitgeliefert", 4.6), aber ohne Konto/Enforcement. `breakglass-approver` und (seit P6-S6) `domain-admin-emergency` bekommen bewusst kein automatisches Konto — die Vier-Augen-Regel aus 4.6 bzw. die Auslöse-Berechtigung aus 4.8 verlangt eine echte, individuell zurechenbare Person, keine geteilte Technik-Identität; Zuweisung an konkrete Menschen läuft über die bestehende, selbst gegatete `POST /role-assignments`-Nutzung in der Admin-UI.

## Not-Shutdown (4.8, seit P6-S6)

Systemweite Notfallsperre + Wartungsmodus — vollständige Architekturbegründung in [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md). Kurzfassung:

- **Auslösen** (`POST /maintenance-mode/trigger`): prüft **immer** direkt die Capability `system.not_shutdown.trigger` bei `triggered_by` über eine neue, aus `_require_permission_if_configured` extrahierte `repository.require_capability()`-Funktion — anders als bei den bisherigen Vier-Augen-Fällen (Bereichssperren, Break-Glass) gibt es hier eine Baseline-Rechteprüfung auch **ohne** aktiviertes Vier-Augen-Prinzip, da 4.8 "frei konfigurierbar, wer auslösen darf" wörtlich verlangt. Ist `ApprovalActionConfig("system.not_shutdown.trigger").requires_approval` gesetzt (Default `false`, **nicht** wie Break-Glass hart auf `true` vorbelegt), läuft die eigentliche Aktivierung stattdessen über den bestehenden Vier-Augen-Mechanismus (P6-S4).
- **Aufheben** (`POST /maintenance-mode/lift`): ausschließlich der aktuell aktive Superuser (4.6) darf aufheben — geprüft über einen neuen `auth_client.py` (`GET /superuser/status` am Auth Service, um `principal_id` erweitert). `403`, falls kein Superuser aktiv ist oder `lifted_by` nicht mit dessen `principal_id` übereinstimmt. **Erster Cross-Service-Aufruf des Permission Service in dieser Richtung** (Auth Service ruft den Permission Service bereits seit P6-S5 auf) — bewusst kein Docker-Compose-`depends_on` dafür, da der Aufruf request-zeitlich ist, nicht beim eigenen Start (siehe ADR 0024).
- **Selbst-Konsum**: bei aktivierter Genehmigungspflicht konsumiert dieser Service `permission.approval.approved` für `action_type="system.not_shutdown.trigger"` selbst (dritter Zweig in `approval_consumer.py`, gleiches Prinzip wie die Bereichssperren).
- **Durchgesetzt wird die Sperre nicht hier, sondern vom Gateway** (`docs/services/gateway-service.md`) — dieser Service liefert nur den Zustand (`GET /maintenance-mode`) und die Aktivierungs-/Aufhebungs-Logik.

## Batch-Check (seit P5-S4, Search Service)

`POST /check/batch` (Body: `{principal_id, permission, access_type, resource_ids: [...]}`, Antwort: `{results: {resource_id: bool}}`) wurde für den neuen Search Service ergänzt: eine Suchergebnisliste kann viele verschiedene Ordner betreffen, und der bestehende `GET /check` prüft nur ein Principal/Resource/Permission-Tripel je Aufruf — viele Einzel-Roundtrips wären für eine Ergebnisseite unpraktikabel. Die Implementierung wiederholt exakt dieselbe Logik wie `/check` (inkl. Bereichssperren-Überlagerung) in einer Schleife über die (deduplizierten) `resource_ids` — jeder Durchlauf trifft den bereits vorhandenen `effective_permission_cache`, daher keine teure Mehrfachberechnung trotz der Schleife; eine SQL-seitige Mega-Query wäre für den hier relevanten Umfang (Suchergebnisseiten, keine Massenoperationen) Overengineering. Search Service nutzt Ordner-`resource_id`s (nicht Dokument-IDs) als Prüfobjekt — Dokumente sind selbst keine Permission-Resources, siehe `docs/services/search-service.md`.

## Vererbungsalgorithmus

Von der angefragten Ressource aus wird die Vorfahrenkette (`parent_id`) nach oben durchlaufen, an jedem Knoten werden die Zuweisungen des Principals gesammelt. Ein Knoten mit `inherit=false` beendet den Aufstieg **nach** Auswertung seiner eigenen Zuweisungen — Standard-DMS-Verhalten (SharePoint/Alfresco), wie in Konzept 4.1 gefordert.

## "everyone"-Gruppe (Post-Roadmap Phase 19, seit Session 2, siehe [ADR 0067](../adr/0067-everyone-gruppe-permission-service.md))

`principal_type="group"` war bis zu dieser Session reine Schema-Deko — `_collect_effective_roles` prüfte
ausschließlich `RoleAssignment.principal_id == principal_id`. Seit Session 2 wird an jedem durchlaufenen
Resource-Knoten zusätzlich geprüft, ob eine Zuweisung mit `principal_type="group",
principal_id="everyone"` existiert — **jeder** authentifizierte Principal gilt dafür implizit als
Mitglied, unabhängig von seiner eigenen `principal_id`. Der Vererbungsalgorithmus selbst (Vorfahrenkette,
`inherit=false` stoppt den Aufstieg) gilt für "everyone"-Zuweisungen identisch zu Einzelzuweisungen.

- **`repository.ensure_everyone_role`** (Bootstrap, Lifespan, gleiches idempotentes Muster wie
  `ensure_domain_admin_roles`) legt sowohl die `Role("everyone")` als auch ihre `RoleAssignment` an der
  Wurzelressource an — anders als Domain-Admin-Rollen hat "everyone" kein externes Konto, dem die
  Zuweisung sonst zugeordnet würde.
- **Aktuell geseedete Berechtigungen**: `users.lookup`, `users.directory` — entsprechen den beiden in
  `auth-service` seit P14-S6/P15-S4 hartkodiert offenen Endpunkten (`GET /users/lookup`, `GET
  /users/directory`, bislang ohne jede RBAC-Prüfung). **Diese Session ändert `auth-service` selbst noch
  nicht** — die eigentliche Umstellung der beiden Endpunkte auf eine echte `has_permission`-Prüfung folgt
  in P19-S3. **Seit P19-S5** ([ADR 0070](../adr/0070-case-service-rbac.md)) zusätzlich `case.read`/
  `case.write` — `case-service` hatte zuvor gar keine Berechtigungsprüfung, die Erweiterung erhält das
  bisherige De-facto-offene Verhalten. **Seit P19-S7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md))
  zusätzlich `archival.read`, `archival.write`, `reporting.read`, `reporting.write`,
  `reporting.forensic_trace` — gleiches Prinzip für `archival-service`/`reporting-service`. **Seit P19-S8**
  ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)) zusätzlich `ocr.read`, `ocr.write`,
  `rendering.read`, `rendering.write` — gleiches Prinzip für `ocr-service`/`rendering-service`.
  **Bewusst NICHT enthalten**: `admin.quarantine` (`virus-scan-service`, dieselbe Session) — anders als
  die übrigen war der Quarantäne-Bereich schon vor P19-S8 eine echte, auf eine dedizierte Rolle
  (`domain-admin-virus-scan`) beschränkte Berechtigung, keine bislang de-facto offene Lücke. **Wichtig für
  künftige Erweiterungen dieser Liste**:
  `ensure_everyone_role` aktualisiert eine bereits angelegte "everyone"-Rolle NICHT automatisch (kein
  Migrationsmechanismus, siehe dortiger Docstring) — auf einer bereits laufenden Installation muss eine
  neue Berechtigung einmalig manuell per `PUT /roles/{id}` nachgezogen werden.
- **Kein vollständiges Gruppenverwaltungssystem**: `"everyone"` ist die einzige reservierte
  Gruppen-Kennung, keine benutzerdefinierten Gruppen mit eigener Mitgliederverwaltung. Echte
  Gruppenmitgliedschaft (z. B. "Nutzer X ist Mitglied von AD-Gruppe Y") bleibt weiterhin ungelöst, siehe
  "Offene Punkte" unten.

## Struktur-Synchronisation (Vertrag bestätigt seit P3-S3)

Der Folder Service (P3-S3) implementiert genau den in P2-S2 provisorisch angenommenen Vertrag — keine Anpassung nötig. `structure_consumer.py` abonniert `settings.structure_subjects` (Default `["folder.>"]`) über `NatsEventBusClient(ensure_stream=False)`:

| event_type (Suffix) | payload |
|---|---|
| `*.resource.created` | `{resource_id, parent_id, resource_type}` |
| `*.resource.moved` | `{resource_id, new_parent_id}` |
| `*.resource.deleted` | `{resource_id}` |

Live end-to-end verifiziert (P3-S3): ein über die echte Folder-Service-API angelegter Ordner erscheint unmittelbar als `ResourceNode` in diesem Service, inklusive korrektem `parent_id`.

**Bekannte Grenze**: Existiert beim Start noch kein Stream für ein konfiguriertes Subject (kein Producer je gelaufen), wird das Subject übersprungen (`SubjectNotFoundError` abgefangen, siehe `dms-eventbus-client`/ADR 0001) statt den Service-Start zu blockieren — es gibt aber keinen Retry-Loop, der den Stream später automatisch nachzieht; ein Neustart ist dann nötig. In der Praxis unkritisch, da der Folder Service inzwischen existiert und seinen Stream beim eigenen Start anlegt.

## Stellvertretung bei Abwesenheit (4.4a, seit P14-S11)

Zeitlich befristete, umfangsbegrenzte Übertragung der Aufgabenwahrnehmung von einer abwesenden Person (`delegator_principal_id`) an eine Stellvertretung (`deputy_principal_id`) — bewusst KEIN Identitätswechsel: die Stellvertretung handelt weiterhin unter dem eigenen Konto, dieser Datensatz ist nur die Grundlage für die Berechtigungsprüfung und den Audit-Vermerk "im Auftrag von". Vollständige Begründung, insbesondere warum Delegation hier statt in `workflow-service` lebt und warum nur `scope_process_definition_ids` tatsächlich ausgewertet wird: [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md).

- **`POST /delegations`** ist ein Selbstverwaltungs-Endpunkt — `delegator_principal_id` kommt immer aus `X-DMS-Principal`, niemand kann eine Delegation im Namen einer dritten Person anlegen.
- **`GET /delegations/check`** ist der einzige echte Durchsetzungspunkt — `workflow-service`s `POST .../tasks/{id}/complete` ruft ihn auf, wenn ein Abschluss `on_behalf_of_principal_id` mitschickt (siehe `docs/services/workflow-service.md`).
- **Widerruf** (`DELETE /delegations/{id}`) nur durch die vertretene Person oder `X-DMS-Roles: dms-admin` (konfigurierbar, `delegation_revoke_admin_role`) — NICHT durch die Stellvertretung selbst.
- `admin-ui` (`/delegations/`) bietet eine reine installationsweite Übersicht + Admin-Widerruf; Anlegen bleibt ausschließlich Selbstverwaltung (`user-ui`s `DelegationsPane`).

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
| `permission.maintenance_mode.activated` | `{triggered_by, reason}` (seit P6-S6, 4.8) |
| `permission.maintenance_mode.lifted` | `{lifted_by}` (seit P6-S6, 4.8) |
| `permission.delegation.created` | `{delegation_id, deputy_principal_id}` (seit P14-S11, 4.4a) |
| `permission.delegation.revoked` | `{delegation_id}` (seit P14-S11, 4.4a) |

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- **Echte, benutzerdefinierte Gruppenmitgliedschaft wird weiterhin nicht aufgelöst** (seit Phase 19 Session 2 nur teilweise behoben, siehe "'everyone'-Gruppe" oben): `principal_id` einer Zuweisung muss weiterhin exakt dem abgefragten Principal entsprechen — mit der einzigen Ausnahme der reservierten `"everyone"`-Kennung, für die JEDER Principal implizit als Mitglied gilt. Eine allgemeine Auflösung "Nutzer X ist Mitglied von Gruppe Y, die Rolle Z hat" (beliebige, admin-definierte Gruppen) ist weiterhin nicht Teil des Systems (hängt von AD-Gruppen-Sync im Auth Service, 4.4, ab, der ebenfalls noch offen ist).
- Granularere Cache-Invalidierung (nur betroffener Teilbaum statt gesamter Cache) als spätere Optimierung möglich, ohne die API zu ändern.
- **Vier-Augen-Prinzip (4.3) ist seit P6-S4 generisch verfügbar, seit P6-S5 auch mit optionaler Rollenbindung (`required_permission`)** — verdrahtet für Bereichssperren, Document-Service-Force-Unlock und Superuser-Break-Glass (`auth.superuser.activate`). Rechte-/Rollenänderungen (`POST /role-assignments`, `POST /roles`) nutzen ihn weiterhin nicht, siehe "Vier-Augen-Approval-Mechanismus" oben.
- **`scope_object_type_ids`/`scope_folder_resource_ids` einer Delegation (4.4a, seit P14-S11) werden aktuell von keinem Endpunkt ausgewertet** — nur `scope_process_definition_ids` ist bei `GET /delegations/check` tatsächlich wirksam (siehe ADR 0048). Die beiden anderen Felder werden mitgespeichert (Konzept-Wortlaut vollständig abgebildet), aber bräuchten einen zusätzlichen Cross-Service-Umweg über `business_key`, um bei einem konkreten Aufgabenabschluss ausgewertet zu werden — nicht Teil dieser Session.
- ~~"Ich vertrete"/"Im Auftrag von"-Anzeigen (4.4a) zeigen rohe Principal-IDs, keine Nutzernamen~~ — **behoben in Post-Roadmap Phase 19 Session 4** ([ADR 0069](../adr/0069-rueckwaerts-identitaetsaufloesung.md)): neuer `GET /users/{id}`-Rückwärtsauflösungs-Endpunkt in `auth-service`, `user-ui`s `DelegationsPane` nutzt ihn jetzt für beide Listen ("Meine Stellvertretungen"/"Ich vertrete").
- ~~Keine Durchsetzung, wer Bereichssperren setzen/aufheben darf~~ — **behoben in Post-Roadmap Phase 19 Session 6** ([ADR 0071](../adr/0071-permission-service-self-gating.md)): `POST`/`DELETE /scope-locks` prüfen `locked_by`/`released_by` jetzt gegen `admin.user_management`, vor dem bestehenden Vier-Augen-Zweig.
- **Kein Ausführungs-Rückkanal / keine Genehmigenden-Benachrichtigung** für den Approval-Mechanismus — siehe [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) "Konsequenzen".
- `GET /check` verlässt sich auf den Aufrufer, den korrekten `access_type` (`read`/`write`) mitzugeben — der Service selbst kennt keine feste Zuordnung Permission-Name → Zugriffsart.
- ~~`POST`/`GET`/`PUT /roles` und `POST`/`GET`/`DELETE /role-assignments` bleiben ungated~~ — **`POST`/`PUT /roles` behoben in Post-Roadmap Phase 19 Session 6** (ADR 0071, `admin.user_management`). `GET /roles` sowie alle drei `/role-assignments`-Endpunkte bleiben weiterhin bewusst ungegatet: `GET /roles` wird von vielen Services für Rollen-Get-or-Create per Name vorausgesetzt, `POST /role-assignments` hätte den Bootstrap-Seeding-Aufruf von `auth-service` (der die allererste Rollenzuweisung anlegt) vor das in ADR 0023 beschriebene Henne-Ei-Problem gestellt (siehe ADR 0071 "Begründung" für die Bestätigung, dass dieses Problem `/roles` selbst nicht betrifft). Der eigentliche Konfigurationsimport, der `PUT /roles/{id}` in der Praxis aufruft, war schon vorher selbst gegatet (`config-service`, `admin.object_config`) — hält seit dieser Session zusätzlich `admin.user_management`.
- **Kein genereller Superuser-Bypass für `require_capability`** (seit Post-Roadmap Phase 19 Session 6, ADR 0071 "Begründung"): nur `POST /maintenance-mode/lift` hat eine hartkodierte Superuser-Sonderprüfung. Ein Break-Glass-Superuser ohne explizite `admin.user_management`-Zuweisung kann seit dieser Session keine Rollen mehr anlegen/ändern oder Bereichssperren setzen/aufheben — eine größere, architektonische Änderung außerhalb bisheriger Sessions.
- **5 der 7 Domain-Admin-Rollen aus 4.6 ohne zugeordnetes technisches Konto** (seit P6-S5/S6): siehe "Domänengetrennte Admin-Rollen" oben — folgt jeweils mit der künftigen Retrofit-Session der betreffenden Domäne.
- **Federation Hub (7.4) und Plugin-Instanzen (3.8) existieren nicht** (4.8, seit P6-S6): der Wartungsmodus kann daher weder "Föderations-Vorgänge pausieren" noch "Plugin-Instanzen anhalten" — beide Wirkungen aus 4.8 bleiben unimplementiert, siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).
- **Kein systemweites Schreibverbot jenseits des Gateways** (4.8, seit P6-S6): direkte Service-zu-Service-Schreibaufrufe am Gateway vorbei sind während des Wartungsmodus weiterhin möglich, siehe ADR 0024.
- **Keine erhöhte Auditierungspriorität für Not-Shutdown-Events** (4.8, seit P6-S6): `AuditEvent` hat weiterhin kein Prioritätsfeld, siehe ADR 0023/0024.
