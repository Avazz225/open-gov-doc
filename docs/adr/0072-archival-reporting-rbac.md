# 0072 — Archiv-/Berichts-RBAC (archival-service, reporting-service)

**Status:** akzeptiert (Session 7 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 7, betrifft `archival-service`, `reporting-service`,
`permission-service`

## Entscheidung

`archival-service` (bis auf das separate, engere `archive_retrieval_role`-Gate für Rückholung/
Aussonderungs-Zugriffsbereich/Paket-Download) und `reporting-service` (inkl. Forensik-Trace) hatten
bislang **GAR KEINE** allgemeine Berechtigungsprüfung. Diese Session schließt beide Lücken:

1. **`archival-service`**: neuer `_require_archival_permission(x_dms_principal, *, access_type)`-Helfer
   (`main.py`, erster Konsument von `libs/dms-permission-client` in diesem Service) — `401` ohne
   `X-DMS-Principal`, sonst `PermissionServiceClient.check(..., permission="archival.read"|
   "archival.write", resource_id=ROOT)`, `403` bei Ablehnung. Gilt für ALLE acht Endpunkte
   (`GET/POST /archival-transfers*`, `GET /released-items`, `GET/POST .../case-archival-transfers*`).
   Das bestehende `archive_retrieval_role`-Gate (X-DMS-Roles, Konzept 5.6 "Entschlüsselung nur für
   berechtigte Rollen") bleibt **unverändert und zusätzlich** bestehen — beide Prüfungen laufen
   nacheinander, keine ersetzt die andere.
2. **`reporting-service`**: neuer `_require_reporting_permission(x_dms_principal, *, permission,
   access_type)`-Helfer. Standardberichte/Planungen/Downloads nutzen `reporting.read`/`reporting.write`;
   der Forensik-Trace (`GET /forensic-trace`, `GET /forensic-trace/export`) bekommt die separate, engere
   `reporting.forensic_trace` statt `reporting.read` — eigene Permission wegen seiner erhöhten
   Sensibilität (potenziell umfassende Nutzeraktivitätsoffenlegung, siehe
   docs/services/reporting-service.md "Offene Punkte").
3. **`queried_by`-Spoofing-Lücke beim Forensik-Trace geschlossen**: der bisherige, vom Client frei
   wählbare `queried_by`-Query-Parameter wird ersatzlos entfernt — die Selbst-Auditierung
   (`_record_trace_query`, `reporting.forensic_trace.queried`-Event) nutzt jetzt ausschließlich den
   verifizierten `X-DMS-Principal`-Header als Akteur-Quelle.
4. **"everyone"-Gruppe (ADR 0067) um fünf neue Permissions erweitert**: `archival.read`,
   `archival.write`, `reporting.read`, `reporting.write`, `reporting.forensic_trace` — erhält das
   bisherige De-facto-offene Verhalten beider Services, macht es aber admin-editierbar. Wie in P19-S5/
   P19-S6 dokumentiert ist `ensure_everyone_role` nicht self-healing — die bereits laufende Installation
   wurde einmalig manuell per `PUT /roles/{id}` nachgezogen.

## Begründung

- **Warum `archival.read`/`.write` und `reporting.read`/`.write` statt einer neuen Domain-Admin-Rolle**:
  beide Services sind (wie case-service) keine reinen Admin-Domänen im Sinne von 4.6, sondern
  Geschäftsfunktionen mit lesendem/schreibendem Zugriff — `<domain>.read`/`<domain>.write` ist das
  etablierte Namensmuster (`case.read`/`.write`, `document.read`/`.write`), passend zu `GET /check`s
  `access_type`-Parameter.
- **Warum `reporting.forensic_trace` als eigene, dritte Permission statt `reporting.read`**: der
  Forensik-Trace kann potenziell sensible Nutzeraktivität systemweit offenlegen (5.4b) — eine getrennte
  Permission erlaubt einem Administrator, normale Berichte breiter zu vergeben als den Forensik-Trace,
  ohne beide zwingend zu koppeln. Gleiches Granularitätsprinzip wie `admin.monitoring` vs.
  `admin.object_config` (getrennte Admin-Domänen für getrennte Sensibilitätsstufen).
- **Warum `archive_retrieval_role` NICHT durch die neue RBAC-Prüfung ersetzt wird**: es ist ein
  eigenständiger, wörtlich im Konzept verankerter Mechanismus ("Entschlüsselung nur für berechtigte
  Rollen", 5.6) mit einer eigenen Semantik (wer darf entschlüsselte Inhalte sehen) — Ersetzen hätte eine
  bestehende, Konzept-verankerte Kontrolle geschwächt statt ergänzt. Layering statt Ersetzen ist auch
  das bereits in ADR 0070 etablierte Muster.
- **Warum `queried_by` entfernt statt nur zusätzlich verifiziert**: ein weiterhin vom Client frei
  wählbares Feld hätte trotz der neuen Authentifizierung weiterhin einen irreführenden, unverifizierten
  zweiten "Akteur" im Audit-Trail erlaubt — die einzige verlässliche Akteur-Quelle nach dieser Session
  ist der Header, ein zusätzliches, potenziell widersprüchliches Feld wäre reine Verwirrung ohne
  Sicherheitswert gewesen.

## Ein bereits durch P19-S6 verursachtes, bislang unentdecktes Testregressions-Problem gefunden und behoben

`permission-service`s `PUT /roles/{id}`-Gating (P19-S6, ADR 0071) brach **vier bestehende
`everyone_role_without`-Testfixtures** (`archival-service` [neu in dieser Session], `reporting-service`
[neu], `case-service`, `auth-service`), die diesen Endpunkt ohne `X-DMS-Principal`-Header aufrufen — ein
Regressionsrisiko, das in P19-S6 selbst unentdeckt blieb, da dort nur `permission-service`/
`config-service`/`migration-service` getestet wurden, nicht die Services mit dieser Fixture. Behoben:

- **`case-service`**: neues Testprincipal `ROLE_ADMIN_PRINCIPAL_ID`, dem eine neue
  `_grant_role_admin_permission`-Session-Fixture (analog `_grant_config_admin_permission`) die
  `domain-admin-users`-Rolle zuweist.
- **`auth-service`**: komplexer, da `permission-service`s `principal_id` für das dortige technische
  `users-admin`-Konto die `TechnicalAccount.id` (Integer als String) ist, nicht der Benutzername (siehe
  `main.py`s `ensure_role_assignment(principal_id=account_id, ...)`) — die Fixture löst diese ID jetzt
  über eine **eigene** SQLAlchemy-Engine auf (nicht `app.state.session_factory`, das an TestClients
  internen Event-Loop gebunden ist und aus einer separaten `asyncio.run()`-Fixture heraus mit "attached
  to a different loop" fehlgeschlagen wäre — dasselbe bereits andernorts im Projekt dokumentierte
  asyncpg/pytest-asyncio-Problem, vgl. `reporting-service/tests/test_api.py::poll_env`).
- **`archival-service`/`reporting-service`** (diese Session): gleiches Muster wie `case-service` — je
  eigenes `ROLE_ADMIN_PRINCIPAL_ID` mit `domain-admin-users`-Zuweisung.

## Konsequenzen

- **Tests**: `archival-service` 61 (vorher 55, +6: 401/403-Tests, `everyone_role_without`-Fixture samt
  Rollen-Grant), `reporting-service` 57 (vorher 51, +6, inkl. angepasster Forensik-Trace-Tests nach
  Entfernen von `queried_by`), `auth-service` 96 (unverändert an Testanzahl, aber die drei zuvor durch
  P19-S6 kaputten Tests wieder grün), `case-service` 50 (unverändert an Testanzahl, gleiches Muster).
  `ruff check`/`ruff format --check` clean für alle vier Services.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau von
  `archival-service`/`reporting-service` + Neustart, sowie manuellem `PUT /roles/{id}` der bereits
  laufenden "everyone"-Rolle): `GET /archival-transfers` ohne Header → `401`, mit Principal → `200`;
  `GET /reports/document-volume` ohne Header → `401`, mit Principal → `200`; `GET /forensic-trace` ohne
  Header → `401`, mit Principal → `200`. Beide Container-Logs zeigen saubere Starts ohne Fehler.
- **Kein anderer Service ruft archival-service/reporting-service per HTTP auf** (per Recherche
  bestätigt) — anders als bei case-service (P19-S5) war kein `system:<Service>`-Header-Fix an anderer
  Stelle nötig.
- **`GET /archival-transfers/due-for-archival` existiert bei archival-service nicht** (anders als bei
  case-service) — alle acht Endpunkte dieser Session sind menschlich nutzbare Admin-Ansichten, keine
  reinen Maschine-zu-Maschine-Rückrufe blieben ungegatet.
