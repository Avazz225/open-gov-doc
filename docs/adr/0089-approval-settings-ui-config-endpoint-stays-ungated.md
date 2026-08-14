# 0089 — admin-ui: Vier-Augen-Einstellungsseite, `PUT /approval-config` bleibt bewusst ungegatet

**Status:** akzeptiert (Post-Roadmap Phase 22 Session 3)
**Kontext:** Post-Roadmap Phase 22 Session 3, betrifft `admin-ui`, `permission-service`

## Entscheidung

Neue Admin-UI-Seite `/approval-settings/` (`ApprovalSettings.tsx`) listet alle bei `permission-service`
bereits konfigurierten Aktionstypen (`GET /approval-config`) mit Toggle je Zeile (`PUT
/approval-config/{action_type}`) sowie ein Formular, um einen bislang nicht konfigurierten Aktionstyp
erstmals per Freitext anzulegen — `GET /approval-config` liefert NUR Zeilen mit explizitem
`PUT`-Aufruf, kein fester Katalog aller im System existierenden Aktionstypen (siehe
`docs/services/permission-service.md`).

Beim Bau dieser Seite wurde geprüft, ob `PUT /approval-config/{action_type}` — bislang der einzige
schreibende Endpunkt in diesem Themenbereich ganz ohne Rechteprüfung — analog zu `POST`/`PUT /roles`
(ADR 0071, `admin.user_management`) self-gegatet werden sollte, jetzt, wo er erstmals eine UI bekommt.
**Ergebnis: geprüft, nicht umgesetzt.** Eine Recherche über den kompletten Testbestand ergab, dass
mindestens acht Services (`auth-service`, `config-service`, `folder-service`, `document-service`,
`migration-service`, `permission-service` selbst, `workflow-service`, `webdav-connector`) diesen
Endpunkt direkt als Test-Infrastruktur aufrufen — ohne `X-DMS-Principal`/Capability, exakt das Setup-
Muster, das die neuen Sitzungen dieses Projekts (siehe z. B. Post-Roadmap Phase 21 Session 4s Blast-
Radius-Analyse für `workflow-service`) inzwischen routinemäßig vor einer Breaking Change prüfen.

## Begründung

- **Warum kein Self-Gating in dieser Session**: ein Self-Gating hätte über ein Dutzend Testaufrufstellen
  quer durch das Repo angefasst (jede müsste einen `X-DMS-Principal`-Header mit passender Capability
  ergänzen) — eine Änderung dieser Größenordnung gehört nicht in eine Session, deren eigentlicher Zweck
  eine neue Admin-UI-Seite ist. Gleiches Prinzip wie die bewusste Rückstellung in P21-S4 (dort: Response-
  Shape statt Rechteprüfung), nur diesmal führte die Prüfung dazu, die Änderung ganz zurückzustellen statt
  sie umzugestalten.
- **Warum trotzdem eine UI dafür gebaut wird, obwohl der Endpunkt ungegatet bleibt**: die Konfiguration
  war vorher nur per `curl`/direktem HTTP-Aufruf änderbar — eine Admin-UI-Seite ist unabhängig vom
  Gating-Zustand ein Usability-Gewinn (Plan-Vorgabe dieser Session). Der fehlende Backend-Schutz ist ein
  bestehendes, nicht durch diese Seite verschärftes Risiko (jeder mit Gateway-Zugriff konnte den Endpunkt
  schon vorher per `curl` ansteuern) — die UI macht die Aktion nur bequemer erreichbar, nicht neu möglich.
  Bewusst **keine** clientseitige `RequireCapability`-Attrappe (anders als z. B. `/users/`) — das würde
  eine Durchsetzung vortäuschen, die serverseitig nicht existiert; gleiche Disziplin wie bei
  `ArchivalTransfersView`s Rückholen-Button (dort ebenfalls kein UI-Gate, wo das Backend keins hat).
- **Warum `required_permission` bei jedem Toggle explizit mitgeschickt wird**: `PUT
  /approval-config/{action_type}` überschreibt `required_permission` immer mit dem übermittelten Wert
  (auch `null`, wenn weggelassen) — ein reiner "requires_approval umschalten"-Aufruf ohne dieses Feld
  hätte z. B. `auth.superuser.activate`s Break-Glass-Rollenbindung (`breakglass.approve`) stillschweigend
  gelöscht. `ApprovalSettings.tsx`s `handleToggle` schickt deshalb immer den bereits geladenen
  `required_permission`-Wert der Zeile mit, live gegen den echten Stack verifiziert.

## Konsequenzen

- **Kein Code-Change am Backend** außer einem erklärenden Docstring an `put_approval_config`, der auf
  diese ADR verweist, damit eine künftige Session nicht erneut bei null recherchiert.
- **Dokumentierter, weiterhin offener Sicherheitspunkt**: `docs/services/permission-service.md` "Offene
  Punkte" nennt das Risiko explizit (jeder mit Gateway-Zugriff kann die Vier-Augen-Pflicht für jeden
  Aktionstyp umschalten) als Kandidat für eine künftige, dedizierte Härtungssession, die dann auch die
  Testaufrufstellen mit-migriert.
- **Tests**: `admin-ui` 185 (vorher 179, +6: neue `approval-settings.test.tsx` — Leerzustand,
  Unreachable-Zustand, Auflisten sortiert inkl. `required_permission`/Status, Umschalten inkl. Erhalt von
  `required_permission`, Anlegen eines neuen Aktionstyps, Fehleranzeige beim Umschalten).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von `admin-ui`, kein
  Codeänderung an `permission-service` nötig): ein neuer Aktionstyp `p22s3.test.action` per `PUT` echt
  angelegt, erschien danach in `GET /approval-config`; Umschalten mit explizit mitgeschicktem
  `required_permission=null` bestätigt; ein Umschalten von `auth.superuser.activate` (echte
  Break-Glass-Konfiguration) mit explizit mitgeschicktem `required_permission="breakglass.approve"`
  bestätigte, dass dessen Rollenbindung dabei erhalten bleibt statt gelöscht zu werden — genau das
  Szenario, das ohne das explizite Mitschicken real kaputtgegangen wäre. Kein interaktiver Browser-Test
  (kein Browser/Playwright in dieser Entwicklungsumgebung verfügbar, projektweit etablierte Praxis).
- Doku: neues [ADR 0089](0089-approval-settings-ui-config-endpoint-stays-ungated.md),
  `docs/services/admin-ui.md` (Seiten-Tabelle, neue Sektion "Vier-Augen-Einstellungen",
  Backend-Anbindungstabelle, Tests-Sektion), `docs/services/permission-service.md` ("Offene Punkte"
  ergänzt) aktualisiert.
