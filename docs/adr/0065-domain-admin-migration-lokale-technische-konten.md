# 0065 — Domain-Admin-Migration auf lokale technische Konten

**Status:** akzeptiert (Session 3 von 3, siehe Phase 18 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 18 Session 3, betrifft `auth-service`

## Entscheidung

Aufbauend auf [ADR 0063](0063-auth-entkopplung-lokale-technische-konten-dual-issuer-jwt.md) (lokaler
Token-Issuer) und [ADR 0064](0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)
(Superuser-Migration) migriert diese Session die beiden verbleibenden technischen Konten
(`users-admin`/"Nutzerverwaltung", `config-admin`/"Workflow-Konfiguration") von Keycloak-Nutzern auf
`TechnicalAccount`-Zeilen:

1. **`bootstrap.py`s `_ensure_domain_admin_accounts(admin: KeycloakAdmin)` entfernt** - die Kontoanlage
   passiert jetzt in `main.py`s async Lifespan, direkt neben dem Superuser (ADR 0064), über ein neues
   `domain_admins.py`-Modul (`ensure_domain_admin_account`, `get_technical_account_id`) - strukturell fast
   identisch zu `superuser.py`, mit zwei Unterschieden: `enabled=True` sofort (kein Break-Glass) und
   mehrere, über `role_name` unterscheidbare Konten statt eines Singletons.
2. **`DOMAIN_ADMIN_ACCOUNTS`** in `bootstrap.py` von `list[tuple[username, role_name, last_name]]` auf
   `list[tuple[username, role_name]]` vereinfacht - `last_name` war reine Keycloak-Profil-Pflicht.
3. **Die bestehende Rollenzuweisungs-Schleife in `main.py`s Lifespan bleibt strukturell erhalten** (`for
   username, role_name in DOMAIN_ADMIN_ACCOUNTS: ... ensure_role_assignment(...)`), nur die Quelle der
   `principal_id` wechselt von `admin_users.list_users(keycloak_admin)` auf
   `domain_admins.get_technical_account_id(session_factory, username)`.
4. **`POST /login`/`POST /refresh` brauchten keine Änderung** - beide verzweigen bereits seit ADR 0064
   generisch anhand eines `technical_account`-Lookups nach Benutzername bzw. des `iss`-Claims, unabhängig
   davon, ob das gefundene Konto ein Superuser oder ein Domain-Admin ist.

## Begründung

- **Warum kein eigener "ist das ein Break-Glass-Konto"-Unterschied im Login-Pfad nötig war**: die
  Verzweigungslogik aus ADR 0064 kennt nur "technisches Konto ja/nein", nicht den `account_type` - die
  Enabled-/Expires-Prüfung ist für Domain-Admins (immer `enabled=True`, `expires_at=None`) einfach
  trivial erfüllt, kein Sonderfall im Code nötig.
- **Warum `notification-service`/`signature-service` (authentifizieren sich selbst als `users-admin` via
  generischem `POST /login`) unverändert bleiben**: vor der Umsetzung geprüft (`grep` über alle Services
  nach `"users-admin"`/`"config-admin"`) - jeder Aufrufer nutzt ausschließlich den generischen
  `/login`-Endpunkt mit Benutzername/Passwort, nie eine Keycloak-spezifische API. Die Verzweigung in
  `/login` ist für sie vollständig transparent.
- **Warum kein neuer Testfall für "Keycloak nicht erreichbar" nötig war (anders als in der
  ursprünglichen Phase-18-Planung angedeutet)**: dieser Beweis wurde bereits in ADR 0064 für den
  Superuser erbracht (identischer Mechanismus, identischer `MultiIssuerTokenValidator`) - eine zweite,
  strukturell identische Session-Verifikation für Domain-Admins hätte keinen zusätzlichen
  Erkenntnisgewinn gebracht.

## Ein echtes Testinfrastruktur-Problem, das dabei gefunden und behoben wurde

Die bestehende `_clean_tables`-Fixture (`tests/conftest.py`) leerte `auth.technical_account` bisher vor
JEDEM einzelnen Test. Für den Superuser (ADR 0064) war das unkritisch, da `role_name=None` - keine
Rollenzuweisung gegen `permission-service` nötig. Für Domain-Admins ist das ein echtes Problem: die reale
Testinstallation hat `permission.role_assignment.create` Vier-Augen-pflichtig konfiguriert (bereits
angewendetes eGov-Konfigurationspaket) - `PermissionServiceClient.ensure_role_assignment` dedupliziert
über `(principal_id, role_id, resource_id)`. Bekam `principal_id` bei JEDEM Testlauf eine neue
Auto-Increment-ID (weil die Zeile jedes Mal frisch angelegt wurde), konnte NIE eine bereits genehmigte
Zuweisung wiedergefunden werden - jeder Testlauf hing auf einem neuen, unbeantworteten Genehmigungsantrag
fest. Beim alten Keycloak-Konto trat das Problem nicht auf, weil dessen UUID über die gesamte Testsession
hinweg stabil blieb (nie von `_clean_tables` gelöscht).

Behoben durch zwei Änderungen in `tests/conftest.py`:

- `_clean_tables` löscht `technical_account`-Zeilen jetzt nur noch mit `account_type != 'domain-admin'`
  (`DELETE` statt `TRUNCATE`, da `TRUNCATE` kein `WHERE` kennt) - Domain-Admin-Zeilen bleiben über die
  gesamte Session stabil, alles andere (aktuell nur der Superuser) wird weiterhin pro Test zurückgesetzt.
- Neue session-weite, `autouse`-Fixture `_bootstrap_domain_admin_role_assignments`: setzt
  `permission.role_assignment.create`s Genehmigungspflicht EINMALIG für einen Wegwerf-`TestClient(app)`-
  Start aus (identisches Muster zur bereits bestehenden `role_assignment_immediate`-Fixture, nur
  session- statt testweit), genau das, was eine echte Reviewer-UI-Genehmigung einmalig täte - danach
  bleibt die Installationseinstellung unverändert.

Ein ursprünglicher Versuch, das Problem über Fixture-Parameterreihenfolge zu lösen (`def
domain_admin_auth_headers(role_assignment_immediate, client)`), schlug fehl: pytest garantiert bei zwei
voneinander unabhängigen Fixtures KEINE Ausführungsreihenfolge nach Deklaration - entscheidend ist die
Reihenfolge, in der die aufrufende TESTFUNKTION ihre Fixtures anfordert (viele Tests fordern `client` vor
`domain_admin_auth_headers` an, wodurch die App längst gestartet war, bevor die Genehmigungspflicht
deaktiviert wurde). Die session-weite Fixture umgeht dieses Ordnungsproblem vollständig, da sie
garantiert vor jeder funktionsweiten Fixture läuft.

## Konsequenzen

- **Vollständig live gegen den echten laufenden Stack verifiziert**: nach Neubau des `auth-service`-Images
  (der reine Container-Restart über `scripts/run-tests.sh` verwendet das alte, ungeänderte Image, ein
  `--build` war für die Live-Verifikation nötig) zeigte der `iss`-Claim beider frisch ausgestellter
  Tokens (`users-admin`, `config-admin`) `dms-auth-service-local` statt der Keycloak-Realm-URL. Da
  `permission.role_assignment.create` auch auf der echten Dev-Installation Vier-Augen-pflichtig ist,
  hing die allererste Rollenzuweisung nach der Migration - wie erwartet - auf "pending" (gleiches
  Verhalten wie in der Testumgebung, dort durch die neue Fixture umgangen). Manuell über
  `POST /approval-requests/{id}/approve` genehmigt (genau der Schritt, den die Reviewer-UI in der Praxis
  automatisieren würde) und `auth-service` neu gestartet: `GET /users` mit `users-admin`-Token → 200,
  `GET /me` mit `config-admin`-Token → 200 mit `realm_roles: ["domain-admin-config"]`, jeweils über das
  Gateway (`/api/auth-service/...`).
- **Alte Keycloak-Konten für `users-admin`/`config-admin` bleiben als Karteileichen bestehen** - `POST
  /login` findet das `TechnicalAccount` zuerst und erreicht den Keycloak-Fallback nie mehr. Kein
  automatisiertes Aufräumen in dieser Session (kein Datenverlust-Risiko, da die Konten einfach ungenutzt
  bleiben) - manuelle Bereinigung über die Keycloak-Admin-Console möglich, aber nicht erforderlich.
- **Phase 18 (Auth-Entkopplung von Keycloak) ist damit abgeschlossen** - Superuser UND Domain-Admins
  leben vollständig unabhängig von Keycloaks Erreichbarkeit in `auth-service`s eigener Datenbank. Das
  Henne-Ei-Problem aus ADR 0023 (permission-service self-gating braucht ein technisches Konto, das nicht
  an Keycloak hängt) ist damit für Phase 19 (P19-S6) gelöst.
