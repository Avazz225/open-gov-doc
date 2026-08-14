# teamspace-service

**Verantwortung:** Team-Arbeitsbereich "Teamspace" (Konzept 2.5, P14-S6) — selbstverwalteter, dauerhafter Gruppenarbeitsbereich (eigener Ordner/Dokumente, gemeinsame Termine und Kontakte), bewusst von der Umlaufmappe (2.3, `case-service`) zu unterscheiden: kein sequenzieller Weiterleitungs-/Abschlussvorgang, sondern ein dauerhaftes Gruppenarbeitsgebiet ohne definiertes Ende. Jeder authentifizierte Principal kann einen neuen Teamspace anlegen und Mitglieder einladen, ohne administrative Vorabeinrichtung.

**Konzept-Referenz:** 2.5, 4.1, 8
**Eigenes Postgres-Schema:** `teamspace` (Tabellen `teamspace`, `teamspace_member`, `teamspace_appointment`, `teamspace_contact`)
**ADR:** [0043 — Eigene Mitgliedschaftstabelle statt RBAC-Erweiterung, ergänzende Rollenzuweisung, kein Gruppen-Support](../adr/0043-teamspace-service-membership-and-permission-integration.md)

## Architekturentscheidung: eigenes Zugriffsregime, kein eigener Dokumentspeicher

`teamspace-service` besitzt keinen eigenen Dokument-/Ordnerspeicher (analog `case-service`s opaken `document_id`-Referenzen) — beim Anlegen eines Teamspace wird ein echter `folder-service`-Ordner erzeugt (`parent_id="root"`, ohne `object_type_id`) und dessen `id` als `root_folder_id` gehalten. Das "vom übrigen RBAC-Modell unabhängige Zugriffsregime" (Konzept 2.5 wörtlich) ist eine **eigene, lokale Mitgliedschaftstabelle** (`teamspace_member`) — jeder Endpunkt außer der Neuanlage prüft direkt gegen diese Tabelle (`main.py._require_member`/`_require_manager`), kein Aufruf gegen `permission-service` für die eigentliche Durchsetzung. **Zusätzlich** legt der Service bei jeder Einladung/jedem Entfernen eine ressourcen-skopierte `permission-service`-Rollenzuweisung (`teamspace-member`) auf dem Wurzelordner an bzw. entfernt sie — nicht die primäre Zugriffskontrolle, aber real wirksam für `search-service`, das bereits heute `document.read` auf Ordnerebene prüft. Vollständige Begründung, inkl. der bewussten Entscheidung GEGEN Gruppen-Mitgliedschaft (im gesamten Projekt kein real durchgesetztes Konzept): siehe ADR 0043.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/teamspaces` | Anlegen (`name`, `description`) — jeder authentifizierte Principal darf, kein Capability-Gate. Legt automatisch einen `folder-service`-Wurzelordner an und macht die anlegende Person zum ersten Mitglied (`can_manage_members=true`) |
| `GET` | `/teamspaces` | Nur Teamspaces, in denen der Aufrufer Mitglied ist |
| `GET` | `/admin/teamspaces` | Installationsweite Übersicht (seit **Post-Roadmap Phase 22 Session 5**, [ADR 0090](../adr/0090-teamspaces-admin-overview.md)) — ALLE Teamspaces inkl. `member_count`, unabhängig von der eigenen Mitgliedschaft. `403` ohne `X-DMS-Principal`/ohne die Capability `admin.teamspace_management`, siehe unten |
| `GET` | `/teamspaces/{id}` | Detail — `404` unbekannt, `403` kein Mitglied |
| `DELETE` | `/teamspaces/{id}` | Löscht nur die Teamspace-Metadaten (Mitglieder/Termine/Kontakte), der Wurzelordner bleibt bestehen — `403` ohne `can_manage_members` |
| `POST` | `/teamspaces/{id}/members` | Einladen (`principal_id`, `can_manage_members`) — `403` ohne `can_manage_members`, `409` bei bereits bestehender Mitgliedschaft. Legt zusätzlich die `permission-service`-Rollenzuweisung an |
| `GET` | `/teamspaces/{id}/members` | Mitgliederliste — jedes Mitglied darf lesen |
| `PUT` | `/teamspaces/{id}/members/{principal_id}` | `can_manage_members` ändern — `403` ohne eigene `can_manage_members` |
| `DELETE` | `/teamspaces/{id}/members/{principal_id}` | Die eigene Mitgliedschaft entfernen ("Teamspace verlassen") ist jedem Mitglied erlaubt; andere zu entfernen verlangt `can_manage_members`. Entfernt zusätzlich die `permission-service`-Rollenzuweisung |
| `POST` | `/teamspaces/{id}/appointments` | Termin anlegen (`title`, `description`, `start_at`, `end_at`) — jedes Mitglied darf |
| `GET` | `/teamspaces/{id}/appointments` | Liste, sortiert nach `start_at` |
| `DELETE` | `/teamspaces/{id}/appointments/{appointment_id}` | Jedes Mitglied darf jeden Termin löschen — vollständig geteilt, kein Ersteller-exklusives Recht |
| `POST` | `/teamspaces/{id}/contacts` | Kontakt anlegen (`name`, `email`, `phone`, `note`) |
| `GET` | `/teamspaces/{id}/contacts` | Liste, alphabetisch sortiert |
| `DELETE` | `/teamspaces/{id}/contacts/{contact_id}` | Jedes Mitglied darf löschen |
| `GET` | `/healthz` | Health-Check |

Jeder gegatete Endpunkt verlangt den Header `X-DMS-Principal` (vom Gateway aus dem JWT-`sub`-Claim gesetzt, siehe `gateway-service`) — `403` wenn er fehlt.

## Datenmodell

- `teamspace`: `id` (UUID), `name`, `description`, `root_folder_id` (opake `folder-service`-Referenz), `created_by`, `created_at`/`updated_at`.
- `teamspace_member`: `id`, `teamspace_id` (FK), `principal_id` (Keycloak-`sub`-UUID, **kein** Gruppen-Support, siehe ADR 0043), `can_manage_members`, `invited_by`, `invited_at`. Unique-Constraint auf `(teamspace_id, principal_id)`.
- `teamspace_appointment`: `id`, `teamspace_id` (FK), `title`, `description`, `start_at`/`end_at`, `created_by`, `created_at`.
- `teamspace_contact`: `id`, `teamspace_id` (FK), `name`, `email`, `phone`, `note`, `created_by`, `created_at`. Bewusst ein einfacher, teamspace-lokaler Adressbucheintrag — NICHT der künftige, installationsweite "Kontakte"-Sonderbereich (Konzept 2.5, Phase 15, noch nicht gebaut), siehe Konzept-Tabelle für die Abgrenzung.

## Anbindung an `folder-service`/`permission-service` (`clients.py`)

- `FolderServiceClient.create_folder()` — `POST /folders` mit `parent_id="root"`, bewusst ohne `object_type_id` (Feld ist bei `folder-service` optional, überspringt die sonst live gegen `object-type-service` laufende Validierung vollständig).
- `PermissionServiceClient` — Get-or-Create der Rolle `teamspace-member` (Permissions: `document.read`, `document.write`, `folder.read`, `folder.write`; nur `document.read` wird aktuell von `search-service` tatsächlich geprüft, die übrigen sind forward-kompatibel dokumentiert) nach demselben Muster wie `migration-service`s `apply_role_assignment`. `grant_resource_access()`/`revoke_resource_access()` legen/entfernen die Zuweisung auf dem Teamspace-Wurzelordner.

## Anbindung an `auth-service`: `GET /users/lookup`

Einladen verlangt, einen eingetippten Nutzernamen in die tatsächlich maßgebliche Keycloak-`sub`-UUID aufzulösen (`X-DMS-Principal`/`RoleAssignment.principal_id`). Das bestehende `GET /users` in `auth-service` ist hinter `admin.user_management` gegated - für Teamspace ungeeignet, da jede Person einladen können soll. Neuer, schmalerer Endpunkt `GET /users/lookup?username=` (siehe `docs/services/auth-service.md`): exakte Namenssuche, jeder authentifizierte Nutzer, liefert nur `{id, username}` zurück (kein allgemeines Personenverzeichnis).

## Installationsweite Admin-Übersicht (Post-Roadmap Phase 22 Session 5, [ADR 0090](../adr/0090-teamspaces-admin-overview.md))

`GET /admin/teamspaces` — anders als `GET /teamspaces` (mitgliedschaftsgefiltert,
`repository.list_teamspaces_for_principal`) liefert dieser Endpunkt **alle** Teamspaces
(`repository.list_all_teamspaces_with_member_counts`, `outerjoin` + `GROUP BY` statt eines Filters),
inkl. `member_count` je Zeile statt einer vollständigen Mitgliederliste (eine vollständige Liste würde
`GET /teamspaces/{id}/members` erfordern, das `_require_member` verlangt — für einen reinen
Admin-Übersichts-Endpunkt unverhältnismäßig). Gegated über eine neue `PermissionServiceClient.
has_permission()`-Prüfung (`admin.teamspace_management`, neue vorgeseedete Domäne
`domain-admin-teamspaces` bei `permission-service`, siehe dortige Doku "Domänengetrennte Admin-Rollen")
— erste echte Rechteprüfung in diesem Service (die übrigen Endpunkte prüfen ausschließlich gegen die
eigene `teamspace_member`-Tabelle, `_require_member`/`_require_manager`, kein Cross-Service-Aufruf für
Autorisierung). Teamspaces selbst bleiben unverändert selbstverwaltet (2.5) — kein Capability-Gate für
Anlegen/Beitreten, nur diese neue Übersicht.

## user-ui-Integration

Neue `TeamspacesPane.tsx` (Icon-Rail-Eintrag 👥) — Master-Detail-Ansicht: Liste der eigenen Teamspaces + Neuanlage-Formular links, Mitglieder/Termine/Kontakte + "Ordner öffnen" (navigiert in den regulären Dokumenten-Explorer, `root_folder_id` wird dafür einmalig über `GET /folders/{id}` aufgelöst, gleiches Prinzip wie beim Öffnen eines favorisierten Ordners, P7-S1d) im Detailbereich rechts. Einladen ruft zunächst `lookupUserByUsername()` auf, dann `inviteTeamspaceMember()` mit der aufgelösten UUID.

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Gateway-Routing läuft vollständig dynamisch über `service_type="teamspace-service"`, keine eigene Gateway-Codeänderung nötig.

## Events

Publiziert (Stream `teamspace`):

| event_type | payload |
|---|---|
| `teamspace.created` | `{name, root_folder_id}` |
| `teamspace.deleted` | `{}` |
| `teamspace.member_invited` | `{principal_id}` |
| `teamspace.member_removed` | `{principal_id}` |

Bewusst keine Termin-/Kontakt-Ereignisse — die Mitgliedschaftsereignisse sind die sicherheitsrelevanten (wer kann diesen Bereich sehen), Termine/Kontakte sind reine Fachdaten ohne vergleichbare Audit-Relevanz. Kein eigener Konsument — dieser Service reagiert auf keine Events anderer Services.

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `teamspace.>`.

## Tests

- `uv run pytest services/teamspace-service/tests`: Repository (Anlegen inkl. Ersteller als erstes Mitglied, Duplikat-Ablehnung, kaskadierendes Löschen von Mitgliedern/Terminen/Kontakten beim Löschen eines Teamspace, Mitglieder-/Termin-/Kontakt-CRUD, `NotFoundError`-Fälle inkl. teamspace-übergreifender Verwechslung). API (läuft gegen den echten, laufenden `folder-service`/`permission-service`, kein Mocking): Neuanlage legt einen echten Ordner an und gewährt der anlegenden Person eine echte `permission-service`-Rollenzuweisung, Mitgliedschaftsprüfung (`403` für Nicht-Mitglieder), Einladen/Entfernen inkl. `permission-service`-Verankerung (Zuweisung entsteht/verschwindet tatsächlich), Selbst-Entfernen ohne `can_manage_members` möglich, Entfernen anderer verlangt es, Termine/Kontakte-CRUD. **45 Tests seit Post-Roadmap Phase 22 Session 5** (vorher 41, +4: `GET /admin/teamspaces` ohne Principal/ohne Capability → je `403`, ein Ende-zu-Ende-Test über zwei Teamspaces mit unterschiedlichen Erstellern bestätigt, dass ein Nicht-Mitglied mit der Capability beide inkl. korrekter Mitgliederzahl sieht, plus ein Repository-Unit-Test für `list_all_teamspaces_with_member_counts`).
- **Live-Verifikation dieser Session**: `docker compose up -d --build teamspace-service` gegen den vollständigen Stack, Selbst-Registrierung bei `registry-service` bestätigt (`GET /instances/teamspace-service`), Gateway-Routing bestätigt (`POST /api/teamspace-service/teamspaces` ohne Token → `401`, beweist korrekte Auflösung über den generischen `/api/{service_type}/...`-Proxy).
- **Echte Ende-zu-Ende-Browser-Verifikation** (ephemerer Playwright-Container, siehe `docs/services/user-ui.md` "Team-Arbeitsbereiche"-Abschnitt für den dabei gefundenen Netzwerk-Stolperstein): Login → Teamspace anlegen → Detail öffnen → Mitglied per Nutzername einladen (löst korrekt über `GET /users/lookup` auf) → Termin/Kontakt anlegen → Ordner öffnen wechselt in den Dokumenten-Explorer → als eingeladenes, nicht-verwaltungsberechtigtes Mitglied eingeloggt bestätigt: Löschen-/Einladen-Aktionen sind unsichtbar, "Verlassen" funktioniert → Löschen als Verwaltungsmitglied entfernt den Teamspace. Keine Konsolenfehler. Dabei ein UI-Bug gefunden und behoben (`user-ui`s Löschen-Buttons für Termine/Kontakte zeigten "common.delete" statt "Löschen" — fehlender i18n-Schlüssel).

## Offene Punkte

- **Kein Schutz davor, dass sich das letzte verwaltungsberechtigte Mitglied selbst entfernt** — ein Teamspace kann dadurch unverwaltbar werden (niemand kann mehr einladen/entfernen/löschen). Bewusst nicht behandelt (seltener Randfall).
- **Keine Gruppen-Mitgliedschaft** — Konzept 2.5 nennt sie als Option, im gesamten Projekt existiert aber kein durchgesetztes Gruppen-Konzept (weder Keycloak-Gruppen-Claim noch `permission-service`-Gruppenexpansion). Siehe ADR 0043.
- **`permission-service`-Verankerung ist nicht die primäre Durchsetzung** — nur `search-service` prüft sie heute tatsächlich. Ein direkter `folder-service`/`document-service`-Zugriff auf den Teamspace-Ordner ist NICHT durch die Teamspace-Mitgliedschaft geschützt (bereits projektweit dokumentierte Lücke, hier nicht neu, nur nicht geschlossen).
- **Löschen entfernt nur Teamspace-Metadaten, nicht den Wurzelordner** — bewusste Grenze, echte Ordnerlöschung wäre ein eigenständiges Feature (Aufbewahrung, Vier-Augen, 5.2).
- **`GET /users/lookup` ist ein Existenz-Oracle** — jeder authentifizierte Nutzer kann herausfinden, ob ein bestimmter Nutzername existiert. Für eine interne Verwaltungssoftware mit bekannter Nutzerpopulation als unkritisch eingestuft.
- ~~Kein Admin-UI-Zugang — Teamspaces sind ein reines Endnutzer-Feature in `user-ui`, keine Verwaltungssicht in `admin-ui` (z. B. "alle Teamspaces einer Installation auflisten") vorgesehen~~ — **behoben in Post-Roadmap Phase 22 Session 5** ([ADR 0090](../adr/0090-teamspaces-admin-overview.md)): neuer `GET /admin/teamspaces`-Endpunkt + neue `admin-ui`-Seite `/teamspaces/`, gegated über die neue Capability `admin.teamspace_management`. Weiterhin reine Sichtbarkeit — keine administrativen Aktionen (Löschen/Mitgliederverwaltung) von der Admin-UI aus, das bleibt Selbstverwaltung.
