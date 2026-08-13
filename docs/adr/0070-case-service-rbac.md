# 0070 — case-service RBAC

**Status:** akzeptiert (Session 5 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 5, betrifft `case-service`, `permission-service`,
`archival-service`, `mail-connector`, `infra/docker-compose.yml`

## Entscheidung

`case-service` hatte bislang **gar keine** Berechtigungsprüfung — nicht einmal einen
`X-DMS-Principal`-Header-Check wie andere Services vor ihrer jeweiligen Durchsetzung. Diese Session
schließt diese Lücke:

1. **Neuer `_require_case_permission(x_dms_principal, *, access_type)`-Helfer** (`main.py`) — `401` ohne
   `X-DMS-Principal`, sonst `PermissionServiceClient.check(principal_id=..., resource_id=ROOT,
   permission="case.read"|"case.write", access_type=...)`, `403` bei Ablehnung. **Erster Konsument von
   `libs/dms-permission-client`** (P19-S1) überhaupt — kein eigener, dupliziertes
   `permission_client.py` mehr nötig.
2. **Alle menschlich nutzbaren Endpunkte gegated**: `POST/GET /cases`, `GET /cases/by-vorgangsnummer`,
   `GET /cases/{id}`, `POST/DELETE .../documents`, `GET .../documents`, `POST .../archive-request`
   (menschliche Aktion trotz "instanzverändernd"), `GET .../archive-status`, alle vier Config-Endpunkte
   (`case-archival-config`, `case-number-config`).
3. **Zwei rein interne Maschine-zu-Maschine-Rückrufe bewusst UNGEGATET gelassen**: `GET
   /cases/due-for-archival` und `PUT /cases/{id}/archived` — beide werden ausschließlich von
   `archival-service` aufgerufen, das dafür aktuell keinerlei Identitäts-Header sendet. Exakt dieselbe,
   bereits vorbestehende Lücke wie `document-service`s analoges `PUT /documents/{id}/archived` (ebenfalls
   ungegatet, verifiziert) — eine allgemeine Service-zu-Service-Authentisierung ist eine größere,
   projektweite Entscheidung außerhalb dieser Session.
4. **`resource_id` ist immer `"root"`** — case-service registriert (wie document-service für seine
   Dokumente) keine eigenen Knoten im permission-service-Ressourcenbaum; eine Umlaufmappe hat laut
   Konzept 2.3 ohnehin keinen Ordner-Elternknoten. Eine feingranulare, Umlaufmappen-eigene
   Ressourcen-Hierarchie ist ein größeres, noch offenes Architekturthema (siehe "Konsequenzen").
5. **"everyone"-Gruppe (ADR 0067) um `case.read`/`case.write` erweitert** — erhält das bisherige
   De-facto-offene Verhalten (case-service prüfte vorher NICHTS), macht es aber admin-editierbar statt
   für immer unveränderlich offen. Gleiches Prinzip wie `users.lookup`/`users.directory` in P19-S3.

## Begründung

- **Warum `case.read`/`case.write` statt einer neuen Domain-Admin-Rolle**: Konzept 2.3 beschreibt die
  Umlaufmappe explizit als *"eigenständiges, RBAC- und constraint-fähiges Objekt"* — ein normales,
  RBAC-gesteuertes Geschäftsobjekt wie Dokumente/Ordner, keine Admin-Domäne. `document.read`/`.write`
  ist das etablierte Namensmuster für genau diesen Fall (`<domain>.read`/`<domain>.write`, passend zu
  `GET /check`s `access_type`-Parameter).
- **Warum `POST /cases/{id}/archive-request` gegated wird, `PUT /cases/{id}/archived` aber nicht**:
  ersteres ist laut eigenem Docstring ein *"Manueller Aussonderungs-Trigger"* — eine menschliche Aktion.
  Letzteres ist explizit *"Interner Rueckruf von archival-service"* — kein menschlicher Aufrufer
  existiert, den man prüfen könnte.
- **Warum die "everyone"-Erweiterung statt einer erzwungenen Rollenzuweisung für alle Nutzer**:
  case-service hatte vorher überhaupt keine Prüfung — jeder authentifizierte Nutzer konnte jede
  Umlaufmappe lesen/ändern. Eine erzwungene, engere Default-Rolle hätte das System für jede bestehende
  Installation beim nächsten Neustart lahmgelegt (niemand hätte `case.read`/`case.write`, bis ein Admin
  manuell Rollen zuweist) - "everyone" erhält Kontinuität, exakt das bereits in ADR 0067/0068 etablierte
  Prinzip.

## Ein echtes Deployment-Problem gefunden und behoben

`infra/docker-compose.yml`s `case-service`-Block hatte **kein** `DMS_PERMISSION_SERVICE_BASE_URL` gesetzt
— `Settings.permission_service_base_url` fiel dadurch auf ihren Lokal-Entwicklungs-Default
(`http://localhost:8004`) zurück, der innerhalb des Containers ins Leere zeigt (`localhost` ist dort
`case-service` selbst, nicht die Docker-Compose-Netzwerkadresse von `permission-service`). Jeder gegatete
Aufruf schlug dadurch mit `httpx.ConnectError` (nicht 401/403!) und `500 Internal Server Error` fehl —
erst bei der Live-Verifikation gegen den echten Stack entdeckt (Unit-/Integrationstests laufen mit
explizit gesetzten `TEST_*_URL`-Umgebungsvariablen, die diese Lücke nicht aufdecken). Behoben durch
`DMS_PERMISSION_SERVICE_BASE_URL: http://permission-service:8000` plus `permission-service:
condition: service_started` in `depends_on` (Konsistenz mit allen anderen permission-service-Konsumenten).

## Zwei weitere, unabhängige Regressionen bei anderen Services gefunden und behoben

Zwei bestehende Services rufen case-service-Endpunkte east-west auf, die durch diese Session gegated
wurden, **ohne jemals einen `X-DMS-Principal`-Header zu senden** — beide wären in echtem Betrieb mit
`401` gescheitert:

- **`archival-service`s `CaseClient.get_case`/`.list_document_references`/`.get_archival_config`**
  (`clients.py`) — alle drei riefen zuvor gegatete Endpunkte ohne Header auf.
- **`mail-connector`s `CaseClient.lookup_by_vorgangsnummer`/`.get`/`.add_document_reference`**
  (`case_client.py`) — dieselbe Lücke, betrifft die automatische Vorgangsnummer-Zuordnung eingehender
  Post (2.5/3.3).

Beide sind reine Maschinen-Aufrufer ohne menschlichen Principal (Vorgangsnummer-Matching wird durch
eingehende E-Mail ausgelöst, nicht durch eine Nutzeraktion) — behoben durch einen synthetischen
`X-DMS-Principal`-Header nach dem bereits im Projekt etablierten `"system:<Service>"`-Muster
(vgl. `actor="system:archival-service"` bei publizierten Events): `"system:archival-service"` bzw.
`"system:mail-connector"`. Da die "everyone"-Gruppe `case.read`/`case.write` standardmäßig gewährt,
funktioniert das ohne ein eigenes technisches Konto oder eine explizite Rollenzuweisung.

**Zusätzlich drei direkte, ungeheaderte case-service-Aufrufe in `mail-connector`s eigener Testsuite
gefunden** (`tests/conftest.py::real_case_id`, `tests/test_api.py::_get_case`/`_get_case_documents`) —
diese Hilfsfunktionen rufen den echten, laufenden case-service-Container direkt per `httpx` an (gleiche
"kein Mocking von Sibling-Services"-Testphilosophie wie im gesamten Projekt), fielen aber erst bei einem
zweiten Testlauf **nach** dem Neubau des case-service-Images auf (der erste Lauf traf noch den alten,
ungegateten Container-Stand) - ebenfalls mit demselben synthetischen `X-DMS-Principal`-Header behoben.

## Konsequenzen

- **Tests**: `case-service` 50 (vorher 45, +5: Positiv-/Negativ-Tests für `create_case`/`list_cases`
  `401`/`403`, `archive-request` Auth-Pflicht). Neue Fixtures `case_headers`/`everyone_role_without`
  in `conftest.py` (letztere dupliziert aus `auth-service`s Muster, Projektkonvention). `archival-service`
  59, `mail-connector` 30 — beide unverändert grün nach dem Header-Fix (ihre Tests nutzen Fake-Clients
  für die betroffenen Pfade, decken die reale HTTP-Regression daher nicht ab - nur die Live-Verifikation
  gegen den echten Stack deckte sie auf). `ruff check`/`ruff format --check` clean für alle vier
  Services.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau aller drei
  Services + Docker-Compose-Fix + Neustart): `GET /api/case-service/cases` über das Gateway → `200`
  (mit Token) / `401` (ohne); `GET /case-archival-config` direkt mit `X-DMS-Principal:
  system:archival-service` → `200` mit echten Konfigurationsdaten; `GET /cases/due-for-archival` weiterhin
  ungegatet erreichbar; `mail-connector`s `GET /cases/by-vorgangsnummer` mit `system:mail-connector` →
  `200`.
- **Kein Ressourcen-Baumeintrag für Umlaufmappen** (weiterhin offen, siehe
  `docs/services/case-service.md` "Offene Punkte"): `resource_id="root"` ist ein bewusst grober,
  vorläufiger Kompromiss - eine feingranulare Berechtigungssteuerung je Umlaufmappe (z. B. nur die
  zuständige Abteilung darf lesen/schreiben) bräuchte eine eigene Ressourcen-Hierarchie, analog zum
  bereits offenen Punkt bei Dokumenten. Nicht Teil dieser Session.
- **`document-service`s eigenes, analoges `PUT /documents/{id}/archived` bleibt weiterhin ungegatet** —
  dieselbe, bereits vor dieser Session bestehende Lücke, nicht durch diese Session verursacht oder
  behoben (anderer Service, außerhalb des Sessionsumfangs).
