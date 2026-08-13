# 0067 — "everyone"-Gruppe in permission-service

**Status:** akzeptiert (Session 2 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 2, betrifft `permission-service`

## Entscheidung

`RoleAssignment.principal_type` kannte bislang nur den Kommentar `# "user" | "group"` als Absichtserklärung
— `"group"` wurde von `_collect_effective_roles` nie ausgewertet, ein solcher Wert war faktisch totes
Schema. Diese Session macht `principal_type="group"` für genau einen reservierten Wert
(`principal_id="everyone"`) zu echter Funktion:

1. **`repository._collect_effective_roles`** prüft an jedem durchlaufenen Resource-Knoten zusätzlich zur
   bisherigen `RoleAssignment.principal_id == principal_id`-Bedingung, ob eine
   `(principal_type="group", principal_id="everyone")`-Zuweisung existiert — per `or_`/`and_`-Erweiterung
   der bestehenden Query, keine zweite Abfrage. Jeder Aufrufer (unabhängig von seiner eigenen
   `principal_id`) gilt damit implizit als Mitglied der "everyone"-Gruppe.
2. **Neue `repository.ensure_everyone_role`** (Bootstrap, gleiches Idiom wie `ensure_domain_admin_roles`):
   legt idempotent eine `Role("everyone", permissions=["users.lookup", "users.directory"])` UND die
   zugehörige `RoleAssignment(principal_type="group", principal_id="everyone", resource_id=ROOT)` an —
   anders als Domain-Admin-Rollen hat "everyone" kein externes Konto, dem die Zuweisung sonst zugeordnet
   würde, daher wird die Zuweisung selbst mitgeseedet, nicht nur die Rolle.
3. **`schemas.RoleAssignmentCreate`/`RoleAssignmentOut`s `principal_type`** von `str` auf
   `Literal["user", "group"]` verschärft — passt zum bereits etablierten `Literal`-Stil dieser Datei
   (`BatchCheckRequest.access_type`, `RoleAssignmentActionResult.status`) und macht "group" als echten,
   validierten Wert statt eines reinen Kommentars sichtbar.
4. **`main.py`s Lifespan** ruft `ensure_everyone_role` direkt nach `ensure_domain_admin_roles` auf.

## Begründung

- **Warum genau diese beiden Berechtigungen (`users.lookup`, `users.directory`) geseedet werden**: sie
  entsprechen den beiden heute in `auth-service` hartkodiert offenen Endpunkten (`GET /users/lookup`,
  `GET /users/directory` — beide bewusst OHNE `admin.user_management`-Gate, siehe deren Docstrings, "jeder
  authentifizierte Nutzer darf..."). Diese Session ändert an `auth-service` selbst noch NICHTS (das ist
  P19-S3) — die Rolle wird hier nur bereits mit den passenden Berechtigungsnamen vorbereitet, damit P19-S3
  direkt `has_permission(principal_id, "users.lookup")` aufrufen kann, ohne selbst noch eine Rolle
  anlegen zu müssen.
- **Warum die Zuweisung direkt gegen die Session statt über den (Vier-Augen-fähigen)
  `POST /role-assignments`-Endpunkt erfolgt**: `ensure_everyone_role` läuft als Bootstrap-Infrastruktur im
  Lifespan, exakt wie `ensure_domain_admin_roles` seit jeher - eine Laufzeit-Admin-Aktion ist das nicht,
  eine Genehmigungspflicht wäre hier unpassend (und würde bei einer frisch installierten Instanz mangels
  eines zweiten Admins nie erfüllbar sein).
- **Warum `principal_id="everyone"` statt eines eigenen Gruppen-Konzepts mit mehreren benannten
  Gruppen**: die Roadmap sieht bewusst nur EINE reservierte Gruppen-Kennung vor ("jeder authentifizierte
  Principal ist implizit Mitglied") - ein vollständiges Gruppenverwaltungssystem (benutzerdefinierte
  Gruppen, Mitgliederverwaltung) ist nicht Teil dieser Session und laut Roadmap auch nicht für Phase 19
  vorgesehen. `principal_type="group"` bleibt als Schema-Feld offen für eine spätere Erweiterung, ohne
  dass diese Entscheidung sie vorwegnimmt.
- **Warum keine Änderung an `auth-service` in dieser Session**: die Roadmap trennt bewusst "Mechanismus
  bauen" (P19-S2) von "bestehenden Bypass tatsächlich ersetzen" (P19-S3) — kleinere, unabhängig
  überprüfbare Sessions statt einer großen kombinierten Änderung.

## Konsequenzen

- **Echte Verhaltensänderung in `permission-service` selbst**: JEDER Principal (auch ein nie zuvor
  gesehener, beliebiger String) hat ab sofort `users.lookup`/`users.directory` in seinen effektiven
  Berechtigungen an der Wurzelressource — bestätigt live gegen den echten Stack (`GET
  /effective-permissions/<nie gesehener Principal>/root` liefert `roles: ["everyone"]`,
  `permissions: ["users.directory", "users.lookup"]`). Zwei bestehende `permission-service`-eigene
  API-Tests (`test_full_flow_via_api`, `test_list_role_assignments_filters_by_principal_id`) prüften
  bislang exakte Gleichheit gegen eine leere bzw. rollenspezifische Berechtigungsmenge an der
  Wurzelressource — beide angepasst, um die neue "everyone"-Basismenge einzuschließen, kein
  Verhaltensfehler.
- **Kein anderer Service ist betroffen**: `users.lookup`/`users.directory` sind neue, bislang nirgends im
  Projekt verwendete Berechtigungsstrings (per Grep bestätigt) - jeder bestehende `/check`/`/check/batch`-
  Aufruf prüft eine ANDERE, spezifische Berechtigung und bleibt dadurch unverändert, unabhängig davon,
  dass die zugrundeliegende `permissions`-Liste jetzt zwei zusätzliche Einträge enthält.
- **Idempotent über einen echten Neustart hinweg verifiziert**: `docker compose restart
  permission-service` zweimal hintereinander (nach Image-Neubau) erzeugt keine doppelten `everyone`-
  Rollen/-Zuweisungen (gleiche `id` vor/nach dem zweiten Neustart).
- **`auth-service`s hartkodierte Bypässe (`GET /users/lookup`, `GET /users/directory`) bleiben bis P19-S3
  unverändert bestehen** — diese Session liefert nur die Grundlage, keine Durchsetzung.
