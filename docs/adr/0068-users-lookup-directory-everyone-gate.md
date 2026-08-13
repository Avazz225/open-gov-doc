# 0068 — `GET /users/lookup`/`GET /users/directory` über die "everyone"-Gruppe gegated

**Status:** akzeptiert (Session 3 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 3, betrifft `auth-service`, `permission-service`

## Entscheidung

Aufbauend auf [ADR 0067](0067-everyone-gruppe-permission-service.md) (echte "everyone"-Gruppe in
`permission-service`) ersetzt diese Session die beiden hartkodierten "jeder authentifizierte Nutzer
darf..."-Bypässe in `auth-service` durch eine echte Berechtigungsprüfung:

1. **`GET /users/lookup`/`GET /users/directory`** rufen jetzt vor der eigentlichen Logik
   `_require_permission(user, "users.lookup" | "users.directory", ...)` auf — ein neuer, generischer
   Helfer (`main.py`), der `app.state.permission_client.has_permission(...)` prüft und bei `False` `403`
   liefert. `_require_user_management` (bestehender Domain-Admin-Gate-Helfer) ruft denselben generischen
   Helfer jetzt intern auf, statt die drei Zeilen zu duplizieren.
2. **Am Ist-Verhalten ändert sich nichts**: die in P19-S2 vorgeseedete "everyone"-Rolle gewährt
   `users.lookup`/`users.directory` weiterhin jedem authentifizierten Principal per Default — der
   Unterschied ist rein strukturell: ein Admin kann diese Berechtigung jetzt über die (künftige,
   P22-Bündel) Rollenverwaltung entziehen, ohne Code zu ändern.

## Ein echter Bug gefunden und behoben (ADR 0067s `Literal`-Verschärfung war zu eng)

ADR 0067 verschärfte `schemas.RoleAssignmentCreate`/`RoleAssignmentOut.principal_type` von `str` auf
`Literal["user", "group"]`. Der bestehende Testlauf für `auth-service`/`permission-service` blieb dabei
grün — aber `config-service` und `migration-service` verwenden bereits produktiv einen DRITTEN Wert,
`principal_type="service"` (technische Konten ohne Keycloak-Konto, z. B. `config-service`s
`_CONFIG_ADMIN_PRINCIPAL_ID`), gefunden erst bei der Regression dieser Session (`test_realm_roles.py`s
`authorized_principal`-Fixture in `auth-service` nutzt ebenfalls `"service"` und schlug mit `KeyError:
'role_assignment'` fehl, da `POST /role-assignments` neuerdings `422` statt der erwarteten
`RoleAssignmentActionResult` lieferte). Behoben durch Erweiterung auf `Literal["user", "group",
"service"]` — die ursprüngliche Absicht (echte Validierung statt eines reinen Kommentars) bleibt
erhalten, nur um den bereits genutzten dritten Wert ergänzt.

## Ein zweiter, unabhängiger Bug gefunden und behoben (`update_role` invalidierte den Cache nicht)

Bei der Entwicklung eines Negativ-Tests (Berechtigung gezielt aus der "everyone"-Rolle entfernen, `403`
erwarten) fiel auf: `permission_service.repository.update_role` (`PUT /roles/{id}`) rief — anders als
JEDE andere rechteverändernde Operation in diesem Modul (`create_role_assignment`,
`delete_role_assignment`, `set_resource_inherit`) — `invalidate_cache()` nicht auf. Ein bereits gecachter
Principal hätte eine per Rollen-Update entzogene Berechtigung erst nach einer unabhängigen, zufälligen
Cache-Leerung verloren (z. B. einer völlig anderen Rollenzuweisung irgendwo im System). Unkritisch,
solange Rollen selten und ohne akute Sicherheitserwartung editiert wurden — mit der "everyone"-Gruppe
(ADR 0067, diese Session) editiert ein Admin diese spezifische Rolle jetzt potenziell GEZIELT, um einem
Principal eine Berechtigung sofort zu entziehen. Behoben durch einen `invalidate_cache()`-Aufruf am Ende
von `update_role`, mit Regressionstest. Live gegen den echten Stack bestätigt: `PUT /roles/{id}` (Everyone
ohne `users.lookup`) wirkt sich beim UNMITTELBAR nächsten `GET /users/lookup`-Aufruf über das Gateway aus,
kein Neustart nötig.

## Begründung

- **Warum ein generischer `_require_permission`-Helfer statt zwei weiterer Kopien von
  `_require_user_management`**: drei fast identische 6-Zeilen-Blöcke wären reine Duplikation gewesen -
  der generische Helfer nimmt `permission`/`message` als Parameter, `_require_user_management` wird
  selbst zu einem dünnen Aufrufer davon.
- **Warum kein Negativtest über eine echte, dauerhaft fehlende Berechtigung eines neu angelegten
  Nutzers möglich war**: die "everyone"-Rolle gilt für JEDEN Principal, auch einen frisch angelegten -
  es gibt keinen Nutzer, der sie nicht hätte. Der Negativtest muss daher die geteilte "everyone"-Rolle
  selbst temporär manipulieren (neue `everyone_role_without`-Fixture in `auth-service/tests/conftest.py`,
  gleiches Wiederherstellungs-Muster wie `role_assignment_immediate`).

## Konsequenzen

- **Tests**: `auth-service` 92 (vorher 90, +2 neue Negativ-Tests für `lookup`/`directory` - die beiden
  bestehenden Positiv-Tests bleiben unverändert grün, da die "everyone"-Rolle das Ist-Verhalten per
  Default reproduziert). `permission-service` 125 (vorher 124, +1: `update_role`-Cache-Invalidierung;
  der Literal-Fix selbst brauchte keinen neuen Test, nur die Erweiterung der bestehenden
  Typannotation). `config-service`/`migration-service` unverändert grün nach dem Literal-Fix.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau beider Services):
  `GET /users/lookup`/`GET /users/directory` über das Gateway funktionieren unverändert (200) mit den
  Default-Berechtigungen; nach gezieltem Entzug von `users.lookup` aus der "everyone"-Rolle über `PUT
  /roles/{id}` liefert derselbe Aufruf sofort `403` (`{"detail": "Fehlende Berechtigung 'users.lookup'
  (everyone-Gruppe entzogen?)"}`), `GET /users/directory` bleibt unbeeinflusst (`users.directory` nicht
  entzogen) — beweist sowohl die tatsächliche Durchsetzung als auch die Cache-Invalidierungs-Korrektur
  in einem Aufruf. Danach wiederhergestellt.
- **Ein bereits vorbestehendes, unabhängiges Problem in `config-service`s Testsuite entdeckt, NICHT
  behoben** (außerhalb des Sessionsumfangs): `permission.role_assignment.create` ist auf dieser realen
  Installation Vier-Augen-pflichtig konfiguriert (bereits von `auth-service`s P18-S3-Session
  dokumentiert), `config-service/tests/conftest.py::authorized_principal` setzt das (anders als
  `auth-service`s `role_assignment_immediate`) nicht temporär aus — 11 Tests schlagen dadurch fehl,
  unabhängig verifiziert (Fehlschlag verschwindet vollständig bei temporär deaktivierter
  Genehmigungspflicht, tritt nach Wiederherstellung erneut auf). Nicht Teil dieser Session (anderer
  Service, andere Testinfrastruktur) — als bekannter, vorbestehender Punkt dokumentiert, analog zum
  webdav-connector-PROPFIND-Timeout aus P18-S3.
