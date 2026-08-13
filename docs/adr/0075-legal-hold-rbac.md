# 0075 — Legal-Hold-RBAC (document-service, folder-service)

**Status:** akzeptiert (Session 10 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 10, betrifft `document-service`, `folder-service`,
`permission-service`, `apps/user-ui`

## Entscheidung

`POST /legal-holds` und `POST /legal-holds/{id}/release` hatten in BEIDEN Services (5.2, seit P7-S1/
P7-S1b) **GAR KEINE** Berechtigungsprüfung — nicht einmal einen `X-DMS-Principal`-Header-Parameter.
Diese Session schließt die Lücke:

1. **Neue Domain-Admin-Rolle `domain-admin-legal-hold`** (`admin.legal_hold`,
   `services/permission-service/src/permission_service/repository.py`s `DOMAIN_ADMIN_ROLES`) — Konzept
   5.2 nennt keine eigene Rolle für Legal Hold, eine neue, dedizierte Domäne statt Wiederverwendung von
   `domain-admin-deletion` (siehe "Begründung").
2. **`document-service`**: neuer `_require_legal_hold_permission(x_dms_principal)`-Helfer (`main.py`),
   nutzt eine neue `has_permission`-Methode auf dem bereits vorhandenen lokalen
   `PermissionServiceClient` (bislang nur `check_read`/`check_write` für Freigabelinks/WebDAV-Edit-
   Token, kein `has_permission` für Domain-Admin-Capabilities). Gated: `POST /legal-holds`,
   `POST /legal-holds/{id}/release`. `GET /legal-holds` und `GET /documents/{id}/has-active-hold`
   bleiben bewusst ungegatet.
3. **`folder-service`**: identisches Muster, aber **erster Konsument von `libs/dms-permission-client`
   in diesem Service** — folder-service hatte bislang KEINEN Permission-Service-Client jeglicher Art
   (nur den separaten `ApprovalClient` für Vier-Augen). Gated: dieselben zwei Endpunkte, `GET
   /legal-holds` bleibt ungegatet.
4. **`apps/user-ui`**: `RetentionPanel.tsx`/`FolderRetentionModal.tsx` blenden die Legal-Hold-Buttons
   (Setzen/Aufheben) nur bei `permissions.includes("admin.legal_hold")` aktiv ein — neue
   `getEffectivePermissions`-API-Funktion (portiert aus `apps/admin-ui`) und neues `permissions: string[]`
   auf `AuthContextValue`, befüllt beim Login/Session-Restore analog zu admin-ui. Serverseitiges `403`
   bleibt die eigentliche Durchsetzung, das Frontend ist reines UX (Buttons bleiben sichtbar, damit die
   Hold-Statusanzeige für jeden Betrachter funktioniert, sind aber deaktiviert).

## Begründung

- **Warum eine neue Domain-Admin-Rolle statt `domain-admin-deletion`**: ein Legal Hold VERHINDERT
  Löschung, ein Löschadministrator FÜHRT sie aus — inhaltlich gegensätzliche Zuständigkeiten. Dieselbe
  Person könnte beide Rollen halten, aber sie zu einer einzigen Capability zu verschmelzen hätte eine
  Installation gezwungen, entweder beides oder nichts zu vergeben, ohne die Möglichkeit einer
  Gewaltenteilung (z. B. Rechtsabteilung setzt Holds, IT-Administration führt Löschungen aus).
- **Warum NICHT in der "everyone"-Gruppe (anders als P19-S5/S7/S8s meiste Ziele)**: ein Legal Hold ist
  eine rechtlich bedeutsame, administrative Aktion (5.2, "unabhängig von der regulären Frist ... bei
  laufendem Rechtsstreit") — die bisherige Offenheit war eine ungeprüfte Lücke, kein bewusst gewährtes
  Verhalten, das es zu erhalten gälte (anders als z. B. `case.read`/`.write`, wo case-service
  buchstäblich jedem authentifizierten Nutzer Zugriff gewährte UND das laut Konzept auch weiterhin
  eine reguläre Fachnutzung bleiben sollte).
- **Warum `folder-service` `libs/dms-permission-client` statt eines lokalen Duplikats bekommt**: anders
  als `document-service`/`workflow-service` (beide mit etablierten lokalen Clients samt
  service-spezifischer Zusatzmethoden) hatte `folder-service` noch KEINEN Permission-Service-Client -
  ein neuer Konsument ohne bestehenden Code, den es zu bewahren gälte, folgt daher dem seit P19-S1
  etablierten "neue Konsumenten nutzen die Shared Lib"-Prinzip.
- **Warum `GET /legal-holds` UND `GET /documents/{id}/has-active-hold` ungegatet bleiben**: die
  Hold-Statusanzeige (z. B. `RetentionPanel`s "Legal Hold aktiv (gesetzt von ...)") muss für JEDEN
  Betrachter eines Dokuments/Ordners sichtbar bleiben, nicht nur für Legal-Hold-Administratoren - sonst
  könnte ein regulärer Nutzer nicht erkennen, warum eine Löschung blockiert ist. `has-active-hold` ist
  zusätzlich ein reiner Maschine-zu-Maschine-Rückruf von `archival-service` vor jedem
  Dehydrierungsschritt, ohne menschlichen Principal.
- **Warum das Frontend die Buttons deaktiviert statt zu verstecken**: Konzept-Vorgabe der Session
  ("Buttons nur für berechtigte Rollen aktiv") — die Statusanzeige (wer hat wann warum einen Hold
  gesetzt) bleibt für jeden sichtbar, nur die Handlungsfähigkeit ist eingeschränkt.

## Konsequenzen

- **Tests**: `document-service` 233 (vorher 215, +18: neuer 403-Test plus Header-Ergänzung an drei
  bestehenden Legal-Hold-Tests und einem `has-active-hold`-Test; zusätzlich ein bislang unentdeckter
  P19-S6-Regressionsfund in `_grant_root_permission`, siehe unten), `folder-service` 116 (vorher 112,
  +4, gleiches Muster). `apps/user-ui`: `tsc`/`eslint`/`vitest` (169 Tests, alle grün, vier
  Test-Dateien mussten `getEffectivePermissions`/`permissions` in ihren Mocks nachziehen) und
  `next build` clean.
- **Sechster Regressionsfund aus P19-S6** (nach den vieren aus P19-S7, dem einen aus P19-S8):
  `document-service`s `test_api.py::_grant_root_permission` (genutzt von Freigabelink-/WebDAV-Edit-
  Token-Tests, nicht nur Legal-Hold) rief `POST /roles` ohne `X-DMS-Principal` auf — document-services
  volle Testsuite lief seit P19-S6 nicht mehr vollständig durch, blieb aber unentdeckt, da frühere
  Sessions nie den kompletten Satz betroffener Services in einem Lauf prüften. Behoben mit demselben
  `_grant_role_admin_permission`-Session-Fixture-Muster wie in den Vorsessions.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau von
  `document-service`/`folder-service`/`permission-service` + Neustart zum Seeden der neuen Rolle):
  `POST /legal-holds` ohne Header → `401`, authentifiziert ohne `admin.legal_hold` → `403`, mit
  zugewiesener Rolle → `404` (unbekanntes Dokument, beweist erfolgreiche Autorisierung). Identisch bei
  `folder-service`. `GET /documents/{id}/has-active-hold` weiterhin ungegatet erreichbar (`200`). Beide
  Container-Logs zeigen saubere Starts.
- **Kein Cascading zwischen document-service- und folder-service-Legal-Holds** (unverändert seit P7-S1b,
  bereits dokumentiert) — beide Systeme bleiben bewusst parallel, nicht verschachtelt.
