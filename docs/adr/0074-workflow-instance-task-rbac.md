# 0074 — Workflow-Instanzstart & Task-Abschluss RBAC

**Status:** akzeptiert (Session 9 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 9, betrifft `workflow-service`, `permission-service`,
`case-service`

## Entscheidung

`POST /process-definitions/{id}/instances` (Instanzstart) und `POST /instances/{id}/tasks/{id}/complete`
(Task-Abschluss) waren seit P6-S6 **bewusst** für jeden authentifizierten Principal offen ("normale
Fachnutzung soll keine Domain-Admin-Rolle brauchen", dokumentierte Nutzerentscheidung). Diese Session
macht daraus eine echte, admin-editierbare RBAC-Prüfung statt eines hartkodiert offenen Pfads:

1. **Neuer `_require_workflow_permission(x_dms_principal, *, access_type)`-Helfer** (`main.py`) — `401`
   ohne `X-DMS-Principal`, sonst Prüfung von `workflow.write` über `PermissionServiceClient.check`
   (`resource_id="root"`, `access_type="write"`), `403` bei Ablehnung. Läuft in beiden Endpunkten NACH
   `_reject_during_maintenance` (4.8 bleibt die äußerste Sperre) und VOR allen anderen Prüfungen
   (`_require_valid_signature_if_needed`, `_reject_manual_federated_completion`,
   `_require_delegation_if_on_behalf_of`) — Basis-RBAC zuerst, spezifischere Checks danach.
2. **`workflow-service`s lokaler `PermissionServiceClient` (eigene Kopie, nicht `libs/dms-permission-
   client`) bekommt eine neue `check()`-Methode** — gleiche Signatur wie die Shared Lib, ergänzt die
   bereits vorhandene `has_permission`/`check_delegation`/`is_maintenance_active`. Kein Umzug auf die
   Shared Lib (ADR 0066 sieht `check_delegation` explizit als bewusst service-eigen vor, ein Voll-Umzug
   hätte hier keinen Mehrwert).
3. **"everyone"-Gruppe (ADR 0067) um `workflow.write` erweitert** — erhält das bisherige,
   dokumentiert-deliberate offene Verhalten, macht es aber admin-editierbar statt für immer
   hartkodiert offen.
4. **`case-service`s `WorkflowClient.start_instance`** (der einzige echte HTTP-Aufrufer von
   Instanzstart außer migration-service) sendete bislang gar keinen `X-DMS-Principal`-Header — bekommt
   einen neuen `x_dms_principal`-Parameter, der den bereits von `_require_case_permission` verifizierten
   Aufrufer durchreicht (NICHT `payload.created_by`, ein ungeprüftes Body-Feld analog zu reporting-
   services `queried_by`-Antipattern, siehe ADR 0072).

## Begründung

- **Warum `workflow.write` statt zweier getrennter Permissions für Start/Abschluss**: beide sind
  gleichrangige, reguläre Fachnutzungs-Schreibaktionen innerhalb derselben Domäne (Workflow-Ausführung)
  — keine der beiden ist sensibler als die andere (anders als reporting-services Forensik-Trace vs.
  Standardberichte), eine Aufspaltung wäre unnötige Granularität ohne erkennbaren Nutzen.
- **Warum in der "everyone"-Gruppe statt einer neuen Domain-Admin-Rolle**: die ursprüngliche P6-S6-
  Entscheidung war explizit *"normale Fachnutzung soll keine Domain-Admin-Rolle brauchen"* — eine neue
  Pflicht-Rolle hätte diese Entscheidung durch die Hintertür kassiert. "everyone" reproduziert exakt das
  bisherige Verhalten, macht es aber zum ersten Mal admin-editierbar (ein Admin kann `workflow.write`
  künftig gezielt aus "everyone" entfernen und stattdessen eine engere Rolle vergeben, ohne Codeänderung).
- **Warum `case-service` den echten `x_dms_principal` statt eines synthetischen `system:case-service`
  durchreicht (anders als z. B. `archival-service`s `CaseClient`)**: eine Umlaufmappen-Erstellung IST
  eine echte, auf einen menschlichen Aufrufer zurückführbare Aktion — `create_case` hat den verifizierten
  Principal bereits aus `_require_case_permission` zur Hand, ein synthetisches Servicekonto würde die
  Audit-Spur unnötig verwässern (anders als bei rein Consumer-getriebenen Aufrufen ohne jeden
  menschlichen Auslöser, z. B. `rendering-service`s OCR-Abfrage).
- **`migration-service` brauchte keine Änderung**: sein `WorkflowServiceClient` sendet bereits
  standardmäßig `X-DMS-Principal: migration-service` (seit P12-S2) — "everyone" deckt diesen Principal
  automatisch mit ab, ohne zusätzlichen Rollen-Grant.

## Konsequenzen

- **Tests**: `workflow-service` 171 (Testanzahl unverändert, aber `client`-Fixtures in `test_api.py`/
  `test_federation.py`/`test_license_gate.py` (inkl. einer eigenständigen `TestClient`-Instanz für den
  `raise_server_exceptions=False`-Testfall) tragen jetzt standardmäßig einen `X-DMS-Principal`-Header;
  ein Test, der spezifisch den 401-Pfad bei fehlendem Header innerhalb der Delegations-Prüfung beweisen
  wollte, überschreibt den Header jetzt explizit auf leer — die Assertion bleibt unverändert `401`, nur
  der auslösende Mechanismus ist jetzt der neue Basis-RBAC-Check statt der spezifischeren Delegations-
  Prüfung). `case-service` 50, `migration-service` 8, `permission-service` 128, `config-service` 48,
  `reporting-service` 57 — alle unverändert an Zahl, weiterhin grün. `ruff check`/`ruff format --check`
  clean.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau von
  `workflow-service`/`case-service` + Neustart, plus manuellem Nachziehen der laufenden "everyone"-
  Rolle): `POST /process-definitions/{id}/instances` ohne Header → `401`, mit Principal → `201`.
  `case-service`s eigene Testsuite deckt den realen End-to-End-Pfad (`create_case` → `WorkflowClient.
  start_instance` mit durchgereichtem Principal) bereits ab — kein Mocking zwischen den Services.
- **`POST /instances/{id}/retry` bleibt bewusst ungegatet** — der Roadmap-Auftrag nannte explizit nur
  Instanzstart und Task-Abschluss, nicht den Retry-Pfad; außerhalb dieser Session.
- **`created_by`/`completed_by` bleiben weiterhin reine, ungeprüfte Body-Strings** (unverändert seit
  P6-S1) — die Gating-Entscheidung dieser Session betrifft nur, *ob* eine Aktion ausgeführt werden darf,
  nicht, ob der angegebene Name stimmt.
