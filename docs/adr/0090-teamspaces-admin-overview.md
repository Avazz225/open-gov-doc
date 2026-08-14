# 0090 — teamspace-service: installationsweite Admin-Übersicht + neue Domäne `admin.teamspace_management`

**Status:** akzeptiert (Post-Roadmap Phase 22 Session 5)
**Kontext:** Post-Roadmap Phase 22 Session 5, betrifft `teamspace-service`, `permission-service`, `admin-ui`

## Entscheidung

`GET /teamspaces` liefert seit jeher nur die Teamspaces, denen der anfragende Principal selbst angehört
(`repository.list_teamspaces_for_principal`, Join über `teamspace_member`) — Teamspaces sind laut Konzept
2.5 bewusst selbstverwaltet, ohne administrative Vorabeinrichtung. Es gab bislang **keine** Möglichkeit,
installationsweit alle existierenden Teamspaces zu sehen, unabhängig von der eigenen Mitgliedschaft.

1. **Neuer Endpunkt `GET /admin/teamspaces`** (`teamspace-service`) — liefert ALLE Teamspaces
   (`repository.list_all_teamspaces_with_member_counts`, `outerjoin` + `GROUP BY`), je Zeile zusätzlich
   `member_count` statt einer vollständigen Mitgliederliste (spart eine zweite, gegatete
   Mitglieder-Route für einen reinen Übersichts-Endpunkt).
2. **Neue Capability `admin.teamspace_management`**, neue vorgeseedete Domäne
   `domain-admin-teamspaces` (`permission_service.repository.DOMAIN_ADMIN_ROLES`) — gleiches Muster wie
   jede vorherige neue Admin-Domäne (z. B. `domain-admin-license`, P9-S1). `GET /admin/teamspaces` prüft
   diese Capability über einen neuen `PermissionServiceClient.has_permission()` (identisches Muster wie
   `document_service.permission_client.PermissionServiceClient.has_permission`).
3. **Neue Admin-UI-Seite `/teamspaces/`** (`TeamspacesAdmin.tsx`) — reine Statustabelle (Name,
   Beschreibung, Angelegt von, Mitgliederzahl, Angelegt am), `RequireCapability` UND gegateter
   Sidebar-Eintrag (anders als P22-S3s `ApprovalSettings` — hier existiert eine echte serverseitige
   Durchsetzung, ein clientseitiges Gate täuscht also nichts vor).

## Begründung

- **Warum ein neuer Endpunkt statt `GET /teamspaces` um einen Admin-Modus zu erweitern**: `GET
  /teamspaces` ist bewusst mitgliedschaftsgefiltert — ein optionaler Query-Parameter, der diese Filterung
  bei ausreichender Capability umgeht, hätte denselben Pfad zwei grundverschiedene Sicherheitsmodelle
  tragen lassen (implizite Selbstfilterung vs. explizite Rechteprüfung). Ein eigener Pfad
  (`/admin/teamspaces`, Konvention aus anderen Services übernommen, z. B. `document-service`s
  `/documents/due-for-archival`) macht die Unterscheidung im Routing selbst sichtbar.
- **Warum eine neue, dedizierte Capability statt Wiederverwendung von `admin.user_management`**: Konzept
  4.6 beschreibt explizit domänengetrennte Admin-Rollen — jede neue administrative Fähigkeit bekommt ihre
  eigene Domäne (ADR 0023), gleiches Prinzip wie `domain-admin-license`/`domain-admin-query-console` u. a.
  Eine Wiederverwendung von `admin.user_management` hätte "Nutzer-/Rechteverwaltung" und
  "Teamspace-Aufsicht" fachlich vermischt, obwohl sie unabhängige Zuständigkeiten sind (eine Person kann
  eine ohne die andere brauchen).
- **Warum `member_count` statt einer vollständigen, aufklappbaren Mitgliederliste** (anders als P22-S2s
  Gruppen-UI): der bestehende `GET /teamspaces/{id}/members`-Endpunkt verlangt `_require_member` — ein
  Admin ohne eigene Mitgliedschaft könnte ihn nicht nutzen. Eine zweite, gegatete Mitglieder-Route wäre
  für eine reine Übersichtsseite unverhältnismäßig; die Zählung reicht für den Zweck "wie viele
  Teamspaces gibt es, wie aktiv genutzt werden sie".
- **Verifikationsdetail, das beim Live-Test auffiel (kein Code-Bug, sondern eine Erinnerung für künftige
  Live-Verifikationen)**: `X-DMS-Principal`, das das Gateway aus dem Access-Token setzt, ist der
  Keycloak-`sub`-Claim (bei technischen Kick-Konten wie `users-admin` eine kurze numerische ID, z. B.
  `"2"`), NICHT der Benutzername. Eine Rollenzuweisung direkt gegen `permission-service` per Username
  greift deshalb nicht automatisch für Aufrufe über das Gateway — `GET /auth-service/me` liefert den
  tatsächlichen `sub`-Wert. Frühere Live-Verifikationen dieses Projekts, die Rollen direkt per Username
  zuwiesen, taten dies überwiegend bei Aufrufen, die den `X-DMS-Principal`-Header manuell per `curl`
  gesetzt hatten (Service-zu-Service-Testmuster) statt über das Gateway zu laufen.

## Konsequenzen

- **Migration**: keine (keine neue Tabelle, keine geänderte Spalte).
- **`Teamspace`s Selbstverwaltung bleibt unverändert** — Anlegen/Beitreten/Verwalten bleibt für jeden
  authentifizierten Principal ohne Capability-Gate möglich, nur die neue installationsweite Übersicht ist
  gegated.
- **Tests**: `teamspace-service` 45 (vorher 41, +4: zwei `403`-Fälle ohne Principal/ohne Capability,
  ein Ende-zu-Ende-Test über zwei verschiedene Teamspaces mit unterschiedlichen Erstellern, der bestätigt,
  dass ein Nicht-Mitglied mit der Capability beide sieht inkl. korrekter Mitgliederzahlen, sowie ein
  Repository-Unit-Test); `permission-service` unverändert bei 137 (nur eine neue, vorgeseedete Rolle,
  bereits durch den bestehenden generischen `ensure_domain_admin_roles`-Mechanismus abgedeckt, kein neuer
  Testfall dafür nötig). `admin-ui` 191 (vorher 185, +6: vier neue Tests in `teamspaces-admin.test.tsx`,
  zwei neue Sichtbarkeits-Tests in `admin-sidebar.test.tsx`).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `teamspace-service`/`admin-ui`, `permission-service` zusätzlich neu gebaut, damit die neue
  vorgeseedete Rolle im laufenden Container tatsächlich existiert): `GET /admin/teamspaces` ohne
  Capability → `403`; nach echter Rollenzuweisung an den korrekten `sub`-Principal → `200`; zwei echte
  Teamspaces mit unterschiedlichen Erstellern über den Gateway angelegt, der Admin-Principal (kein
  Mitglied von keinem der beiden) sah in der Übersicht BEIDE inkl. korrekter Mitgliederzahl — bestätigt
  durch einen parallelen `403` auf `GET /teamspaces/{id}` für denselben Principal (die reguläre,
  mitgliedschaftsgefilterte Route bleibt unverändert). Testdaten anschließend gelöscht. Kein
  interaktiver Browser-Test (kein Browser/Playwright in dieser Entwicklungsumgebung verfügbar,
  projektweit etablierte Praxis).
- Doku: neues [ADR 0090](0090-teamspaces-admin-overview.md), `docs/services/teamspace-service.md`
  (API-Tabelle, neue Sektion "Installationsweite Admin-Übersicht", Tests-Sektion),
  `docs/services/permission-service.md` ("Domänengetrennte Admin-Rollen"-Sektion), `docs/services/
  admin-ui.md` (Seiten-Tabelle, neue Sektion "Teamspaces-Admin-Übersicht", Backend-Anbindungstabelle,
  Tests-Sektion) ergänzt.
