# 0073 — OCR-/Rendering-/Virenscan-RBAC

**Status:** akzeptiert (Session 8 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 8, betrifft `virus-scan-service`, `ocr-service`,
`rendering-service`, `permission-service`, `rendering-service`s/`search-service`s/`archival-service`s
HTTP-Clients

## Entscheidung

Drei Lücken, unterschiedlicher Art:

1. **`virus-scan-service`s Quarantäne-Bereich (`GET /scans?status=infected`, `POST /scans/{id}/release`,
   `POST /scans/{id}/purge`) prüfte bislang nur ein reines `X-DMS-Roles`-Stringgleichheits-Gate**
   (`_has_quarantine_role`, `settings.quarantine_admin_role`, Default `"dms-admin"`) — kein echter
   permission-service-Aufruf. Ersetzt (nicht ergänzt) durch `_require_quarantine_permission`: prüft
   `admin.quarantine` über `PermissionServiceClient.has_permission`. Neue Domain-Admin-Rolle
   `domain-admin-virus-scan` (`services/permission-service/src/permission_service/repository.py`,
   `DOMAIN_ADMIN_ROLES`) trägt diese Permission. `quarantine_admin_role`/`DMS_QUARANTINE_ADMIN_ROLE`
   sind ersatzlos entfernt.
2. **`ocr-service` und `rendering-service` hatten GAR KEINE Berechtigungsprüfung.** Neue
   `_require_ocr_permission`/`_require_rendering_permission`-Helfer (identisches Muster wie ADR 0072):
   `ocr.read`/`ocr.write` bzw. `rendering.read`/`rendering.write` an der Wurzelressource (`root`).
3. **Vier reale Konsumenten nachgezogen** — `GET /ocr-results`/`GET /renditions`/`.../content` werden aus
   NATS-Consumer-Kontexten heraus ohne menschlichen Principal aufgerufen: `rendering-service`s
   `OcrServiceClient`, `search-service`s `OcrServiceClient` und `RenderingServiceClient`,
   `archival-service`s `RenderingClient` — alle vier bekommen einen synthetischen
   `X-DMS-Principal: system:<Service>`-Header (etabliertes Muster aus ADR 0070).

## Begründung

- **Warum `admin.quarantine` ERSETZT statt ERGÄNZT (anders als ADR 0072s `archive_retrieval_role`)**:
  der Roadmap-Auftrag für diese Session war wörtlich "auf echte permission-service-Prüfung ... gehoben"
  — eine Anhebung, keine Ergänzung. Anders als bei archival-services `archive_retrieval_role` (das eine
  im Konzept 5.6 eigenständig benannte Kontrolle bleibt) war `quarantine_admin_role` von Anfang an nur
  ein Platzhalter-Mechanismus (reiner String-Vergleich gegen einen ungeprüften Header), kein
  eigenständiges, konzeptionell verankertes zweites Gate.
- **Warum eine NEUE Domain-Admin-Rolle statt Erweiterung der "everyone"-Gruppe**: Konzept 2.5 nennt
  Quarantäne-Zugriff wörtlich als *"eine eigene, eng begrenzte Rolle"* — anders als bei archival-service/
  reporting-service (P19-S7) oder case-service (P19-S5) war dieser Bereich VOR dieser Session bereits
  eine echte, auf `dms-admin` beschränkte Berechtigung, keine bislang de-facto offene Lücke. Die
  "everyone"-Erweiterung (ADR 0067) dient dem Erhalt bisherigen OFFENEN Verhaltens — hier gilt das
  Gegenteil: eine bereits geschlossene Tür bleibt geschlossen, nur der Schlüsselmechanismus wechselt von
  einem Rollen-String zu einer echten permission-service-Rolle.
- **Warum `ocr.read`/`.write`/`rendering.read`/`.write` UND die "everyone"-Erweiterung dafür**: beide
  Services hatten zuvor keinerlei Prüfung — exakt das Muster aus P19-S7 (archival/reporting), "everyone"
  erhält das bisherige De-facto-offene Verhalten.
- **`document-service`s unabhängiges `quarantine_release_admin_role`-Gate bleibt unverändert**: die
  `POST /documents/from-quarantine-release`-Prüfung dort (`_has_quarantine_release_role`, eigenes
  Setting, zufällig ebenfalls Default `"dms-admin"`) ist ein GETRENNTER Mechanismus in einem anderen
  Service, außerhalb dieser Session — `virus-scan-service`s `release_scan` reicht `x_dms_roles`
  weiterhin unverändert durch, obwohl die eigene RBAC-Prüfung jetzt unabhängig davon läuft.

## Konsequenzen

- **Tests**: `virus-scan-service` 32 (Rollen-String-Tests durch RBAC-Positiv-/Negativtests ersetzt, neue
  `_grant_quarantine_permission`-Session-Fixture), `ocr-service` 40 (+8 skipped unverändert),
  `rendering-service` 34, `search-service` 56 (+2 Fixes: `_grant_root_read` brauchte seit ADR 0071 einen
  berechtigten Principal für `POST /roles`, unabhängiger Fund, siehe unten), alle grün.
  `ruff check`/`ruff format --check` clean für alle betroffenen Services.
- **Weiterer, bereits durch ADR 0071 (P19-S6) verursachter Regressionsfund**: `search-service`s
  `test_api.py::_grant_root_read` rief `POST /roles` ohne `X-DMS-Principal` auf — bricht seit P19-S6s
  `PUT`/`POST /roles`-Gating. Fünfter derartiger Fund nach den vier aus ADR 0072 (case-service,
  auth-service, archival-service, reporting-service) — P19-S6 selbst testete nur permission-service/
  config-service/migration-service, sodass dieser Fixture-Typ projektweit unvollständig geprüft war.
  Behoben mit demselben `_grant_role_admin_permission`-Session-Fixture-Muster.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau von
  `virus-scan-service`/`ocr-service`/`rendering-service`/`search-service`/`archival-service` + Neustart,
  plus manuellem Nachziehen der laufenden "everyone"-Rolle und Neustart von `permission-service` zum
  Seeden der neuen `domain-admin-virus-scan`-Rolle): `GET /scans?status=infected` ohne Header → `401`,
  authentifiziert ohne `admin.quarantine` → `403`, mit zugewiesener `domain-admin-virus-scan`-Rolle →
  `200`; `GET /scans` (ohne `status`-Filter) weiterhin ungegatet erreichbar; `GET /config` (ocr-service)
  und `GET /renditions` (rendering-service) je ohne Header → `401`, mit Principal → `200`. Alle
  Container-Logs zeigen saubere Starts ohne Fehler.
- **Kein Ressourcen-Baumeintrag für OCR-Ergebnisse/Renditionen** (wie bei archival-service/
  reporting-service, ADR 0072) — `resource_id="root"` bleibt der projektweit einheitliche Kompromiss für
  Services ohne eigene Ressourcen-Hierarchie.
