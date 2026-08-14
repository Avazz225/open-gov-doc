# 0088 — permission-service: admin-anlegbare Gruppen mit echter Mitgliedschaft

**Status:** akzeptiert (Post-Roadmap Phase 22 Session 2)
**Kontext:** Post-Roadmap Phase 22 Session 2, betrifft `permission-service`, `admin-ui`

## Entscheidung

`RoleAssignment.principal_type="group"` war seit Phase 19 Session 2 ([ADR 0067](0067-everyone-gruppe-permission-service.md))
für genau einen reservierten Wert wirksam (`principal_id="everyone"`, jeder authentifizierte Principal
implizit Mitglied, keine eigene Datenzeile) — jeder andere `"group"`-Wert war weiterhin reine Schema-Deko,
nie ausgewertet. Diese Session ergänzt echte, admin-anlegbare Gruppen mit expliziter Mitgliedschaft:

1. **Zwei neue Tabellen**: `group` (`id` UUID-str, `name` unique, `description`, `created_at`) und
   `group_membership` (`id`, `group_id` FK, `principal_id`, unique auf `(group_id, principal_id)`).
2. **`_collect_effective_roles` erweitert**: sammelt vor dem Durchlaufen der Ressourcen-Vorfahrenkette
   einmalig alle `group_id`s, denen der angefragte `principal_id` per `group_membership` angehört, und
   behandelt an jedem Knoten zusätzlich zu `principal_id == principal_id` und der "everyone"-Bedingung
   jede Zuweisung mit `principal_type="group", principal_id IN (Mitgliedsgruppen)` als zutreffend.
3. **Neue Endpunkte**: `POST`/`GET`/`DELETE /groups`, `GET`/`POST /groups/{id}/members`,
   `DELETE /groups/{id}/members/{principal_id}` — `POST`/`DELETE` gegated über dasselbe
   `_require_role_management` (`admin.user_management`) wie `POST`/`PUT /roles` ([ADR 0071](0071-permission-service-self-gating.md)),
   `GET`-Endpunkte bewusst weiterhin ungegatet (gleiche Begründung wie `GET /roles`).
4. **`apps/admin-ui`**: neue "Gruppen"-Sektion in `UserManagement.tsx` (`/users/`) — Anlegen, Löschen,
   aufklappbare Mitgliederliste je Gruppe (Hinzufügen per freier `principal_id`-Eingabe, Entfernen je
   Zeile).

## Begründung

- **Warum eine eigene Tabelle statt einer Erweiterung des `"everyone"`-Musters**: "everyone" hat bewusst
  KEINE eigene Mitgliederzeile (jeder Principal gilt implizit als Mitglied) — das Muster lässt sich nicht
  auf "eine begrenzte, admin-definierte Teilmenge von Principals" übertragen, ohne eine echte
  Mitgliederliste zu führen. `group`/`group_membership` sind deshalb komplementär zu, nicht anstelle von
  `EVERYONE_PRINCIPAL_ID`.
- **Warum `_group_ids_for_principal` VOR der Schleife über die Vorfahrenkette aufgelöst wird, nicht pro
  Knoten neu**: die Mitgliedschaft eines Principals ist unabhängig von der gerade abgefragten Ressource —
  eine einmalige Auflösung pro `_collect_effective_roles`-Aufruf vermeidet N identische Datenbankabfragen
  bei einer tiefen Ressourcen-Hierarchie, ohne die Semantik zu verändern.
- **Warum Löschen einer Gruppe keine Referenzprüfung gegen bestehende `RoleAssignment`-Zeilen macht**:
  `role_assignment.principal_id` ist ein freier String, keine FK auf `group.id` (`principal_type` kann
  ebenso `"user"`/`"service"` sein) — eine Referenzprüfung würde eine FK-Beziehung vortäuschen, die es
  nicht gibt. Eine verwaiste Zuweisung matcht nach dem Löschen schlicht keinen Principal mehr (leere
  Mitgliederliste), identisches Verhalten zu einer Gruppe, der nie ein Mitglied zugewiesen wurde.
  Konsistent mit `Role`, das ebenfalls keinen Lösch-Endpunkt/keine Referenzprüfung kennt.
- **Warum Mitglied-Hinzufügen idempotent ist statt einen Konflikt zu melden**: passt zum übrigen,
  bewusst fehlerarmen Stil dieses Service (vgl. `ensure_everyone_role`, das ebenfalls prüft-vor-anlegt
  statt auf eine DB-Unique-Constraint-Exception zu vertrauen) — ein Admin, der versehentlich zweimal
  "hinzufügen" klickt, soll keine Fehlermeldung sehen.
- **Warum dieselbe Capability (`admin.user_management`) statt einer neuen, dedizierten Gruppen-Capability**:
  Gruppen sind ein weiterer Baustein der Rechteverwaltung, keine eigenständige Domäne — dieselbe
  Begründung wie ADR 0071 für `POST`/`PUT /roles`. Eine feinere Aufteilung (z. B. "darf Gruppen anlegen,
  aber keine Rollen") ist nicht Teil dieser Session, könnte bei Bedarf später ergänzt werden.
- **Warum keine automatische AD-Gruppen-Synchronisation**: das ist ein separates, größeres Feature
  ("AD-Gruppe → interne Rolle Mapping-Regelengine", geplant als eigenständige **Phase 24 Session 2**) —
  diese Session liefert nur die admin-manuelle Grundlage (echte Gruppen mit Mitgliedschaft), auf der eine
  künftige automatische Synchronisation aufsetzen könnte, ohne selbst schon zu synchronisieren.

## Konsequenzen

- **Migration**: keine (zwei brandneue Tabellen, `Base.metadata.create_all` legt sie automatisch an —
  kein `ALTER TABLE` nötig, da keine bestehende Tabelle verändert wird).
- **Testinfrastruktur-Bug gefunden und behoben**: `tests/conftest.py`s `_clean_tables`-Fixture listet die
  zu leerenden Tabellen einer festen `TRUNCATE`-Anweisung statt sie aus `Base.metadata` abzuleiten — die
  beiden neuen Tabellen fehlten dort zunächst. Ein erster Testlauf lief zufällig grün (keine
  Namenskollision innerhalb des Laufs), ein zweiter, unabhängiger Lauf schlug mit
  `UniqueViolationError` auf `group.name` fehl, da Gruppen aus dem ersten Lauf in der Test-DB
  überlebt hatten. Behoben durch Ergänzen von `permission.group_membership`/`permission.group` in der
  `TRUNCATE`-Liste, danach zwei aufeinanderfolgende Läufe bestätigt grün.
- **Cache-Invalidierung**: jede Mitgliedschafts-/Löschänderung leert den gesamten
  `effective_permission_cache` (gleiche grobkörnige Strategie wie jede andere rechteverändernde
  Operation in diesem Service, siehe README/Docstring an `EffectivePermissionCache`) — ein entferntes
  Mitglied verliert die über die Gruppe gehaltenen Berechtigungen sofort, live bestätigt (siehe unten).
- **Tests**: `permission-service` 137 (vorher 128, +9: Gruppen anlegen/auflisten/löschen inkl.
  Authentisierungs-/Berechtigungsprüfung, Mitglied hinzufügen/idempotent/entfernen inkl. `404`-Fälle, und
  der Kerntest `test_group_membership_grants_role_to_every_member` — eine einzelne Rollenzuweisung an
  eine Gruppe mit zwei Mitgliedern gewährt beiden die Berechtigung, ein Nicht-Mitglied bleibt unbetroffen,
  Entfernen eines Mitglieds entzieht die Berechtigung sofort). `admin-ui` 179 (vorher 175, +4).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `permission-service`/`admin-ui`): eine Gruppe live angelegt, zwei Principals als Mitglieder
  hinzugefügt, eine neue Rolle EINMALIG an die Gruppe (nicht an jeden Principal einzeln) zugewiesen —
  `GET /check` bestätigte die Berechtigung für BEIDE Mitglieder und deren Fehlen für einen Nicht-Mitglied;
  Entfernen eines Mitglieds entzog die Berechtigung sofort (Cache-Invalidierung bestätigt), das
  verbleibende Mitglied behielt sie; Löschen der Gruppe bestätigt über `GET /groups`. Kein interaktiver
  Browser-Test der neuen Admin-UI-Sektion (kein Browser/Playwright in dieser Entwicklungsumgebung
  verfügbar, projektweit etablierte Praxis) — stattdessen über Vitest-Komponententests sowie die
  Backend-API-Verifikation über exakt dieselben Gateway-Aufrufe abgesichert.
- Doku: neues [ADR 0088](0088-admin-defined-groups.md), `docs/services/permission-service.md`
  (API-Tabelle, Datenmodell, neue Sektion "Admin-anlegbare Gruppen", "Offene Punkte" teilweise als
  behoben markiert), `docs/services/admin-ui.md` (Seiten-Tabelle, neue Sektion "Gruppen-Verwaltung",
  Backend-Anbindungstabelle, Tests-Sektion) ergänzt.
