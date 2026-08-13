# 0066 — Geteilte `dms-permission-client`-Bibliothek statt weiterer Duplikate

**Status:** akzeptiert (Session 1 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 1, betrifft `libs/dms-permission-client` (neu)

## Entscheidung

Neues Shared-Package `libs/dms-permission-client` (uv-Workspace-Mitglied über den bestehenden
`members = ["libs/*", "services/*", "tools/*"]`-Glob, kein manueller Eintrag nötig), das die vier über
die zehn bereits im Projekt vorhandenen `PermissionServiceClient`-Duplikate hinweg gemeinsamen
Operationen bündelt:

- `check(*, principal_id, resource_id, permission, access_type="read") -> bool` — verallgemeinert
  `document-service`s bisheriges `check_read`/`check_write`-Paar über den `access_type`-Parameter.
- `check_batch(*, principal_id, permission, access_type="read", resource_ids) -> dict[str, bool]` —
  1:1 aus `search-service`/`query-service` übernommen (identische Implementierung in beiden).
- `has_permission(principal_id, permission) -> bool` — Domain-Admin-Root-Gate, in 7 der 10
  bestehenden Duplikate byte-identisch vorhanden.
- `ensure_role_assignment(*, principal_id, role_name, resource_id="root") -> None` — aus
  `auth-service`s Implementierung übernommen, inklusive der dortigen `RoleAssignmentPendingApprovalError`-
  Prüfung (ADR 0060, Vier-Augen-Prinzip bei `permission.role_assignment.create`) und `RoleNotFoundError`.
  Um `resource_id` erweitert (Default weiterhin `"root"`), damit künftige Konsumenten wie
  `teamspace-service`s ressourcenskopierte Zuweisungen dieselbe Methode nutzen können, ohne eine eigene
  Kopie zu pflegen.

Struktur/Konventionen 1:1 aus `libs/dms-auth-client`/`libs/dms-metrics-client` übernommen: hatchling-
Build, `[tool.uv.sources] dms-permission-client = { workspace = true }` in konsumierenden Services'
`pyproject.toml`, `client: httpx.AsyncClient | None = None`-Konstruktorparameter für Testbarkeit per
`httpx.MockTransport` (Muster aus `dms-metrics-client`s `SensorConfigClient`, da dieses Projekt weder
`respx` noch `pytest-httpx` nutzt).

## Begründung

- **Warum jetzt und nicht früher**: die Duplikation war seit P6-S5 bekannt, aber erst mit der
  "Offene Punkte"-Triage explizit als zu konsolidieren benannt - vorher fehlte der Anlass, da jede
  Kopie unabhängig für ihren jeweiligen Anwendungsfall (Domain-Admin-Gate, Suchfilterung,
  Freigabelink-Prüfung, ...) organisch gewachsen ist.
- **Warum KEINE Zwangsmigration der 10 bestehenden Duplikate**: eine reine Umstellung ohne fachliche
  Änderung wäre Selbstzweck-Refactoring (verstößt gegen die Projektkonvention, siehe `CLAUDE.md`/
  `CONTRIBUTING.md`) und birgt unnötiges Regressionsrisiko für bereits funktionierenden, meist
  ungetesteten Code (siehe "Bekannte Grenze" unten). Fünf der zehn Duplikate haben zudem
  service-spezifische Zusatzmethoden (`workflow-service`s `check_delegation`, `query-service`s
  Vier-Augen-Endpunkte, `teamspace-service`s Rollen-Bootstrap, `config-service`s Rollen-/Approval-
  Verwaltung), die bewusst NICHT Teil der gemeinsamen Bibliothek sind - sie decken jeweils nur einen
  Service ab, eine Aufnahme in die Shared Lib würde deren Oberfläche unnötig aufblähen.
  Migrationskandidaten für spätere, eigenständige Sessions (kein Bestandteil dieser Entscheidung).
- **Warum `resource_id` als Parameter statt hartkodiert `"root"`**: `auth-service`s Original kannte nur
  Wurzelressourcen-Zuweisungen (Superuser/Domain-Admins). `teamspace-service`s
  `grant_resource_access`/`revoke_resource_access` weisen dieselbe Operation aber auf einer konkreten
  Teamspace-Wurzelordner-Resource zu - ein optionaler Parameter mit sinnvollem Default deckt beide Fälle
  ab, ohne zwei Methoden pflegen zu müssen.
- **Warum `httpx.MockTransport` statt `respx`/`pytest-httpx`**: keine der beiden Bibliotheken ist
  bereits eine Projektabhängigkeit; `httpx.MockTransport` ist Teil von `httpx` selbst (bereits überall
  vorhanden) und deckt den Bedarf (Request-Inspektion + kontrollierte Antworten) vollständig ab -
  bestätigt durch das bereits etablierte, identische Muster in `libs/dms-metrics-client`s
  `SensorConfigClient`-Tests und mehreren Service-Tests (`workflow-service/tests/test_license_client.py`
  u. a.).

## Konsequenzen

- **Erste echte Testabdeckung eines `PermissionServiceClient`-artigen HTTP-Clients in diesem Projekt**:
  keines der 10 bestehenden Duplikate (auch nicht `document-service`s bereits produktiv genutzte
  `check_read`/`check_write`) hatte zuvor eigene Unit-Tests - 12 neue Tests decken alle vier Methoden der
  Shared Lib ab (inkl. Idempotenz-Pfad, Pending-Approval-Pfad, leere `resource_ids`-Kurzschluss).
- **`uv.lock` aktualisiert** (`uv lock`), neues Package als 152. Workspace-Mitglied aufgenommen. Kein
  Dockerfile eines bestehenden Service musste geändert werden - `COPY libs/ libs/` erfasst das neue
  Verzeichnis automatisch, sobald ein Service es künftig als Abhängigkeit deklariert.
- **Kein Service nutzt die Bibliothek in dieser Session** - reine Infrastruktur-Session, analog zu
  P18-S1. Der erste tatsächliche Konsument folgt mit den kommenden Phase-19-Sessions (Gating neuer
  Endpunkte), bestehende Duplikate bleiben unverändert bestehen.
- **Bekannte Grenze**: `workflow-service`s `check_delegation`, `query-service`s Vier-Augen-Client-
  Methoden, `teamspace-service`s Rollen-Bootstrap und `config-service`s Rollen-/Approval-Verwaltung
  bleiben weiterhin dupliziert bzw. service-eigen - keine Konsolidierung in dieser Session vorgesehen.
