# 0071 — permission-service self-gating (Rollen/Bereichssperren)

**Status:** akzeptiert (Session 6 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 6, betrifft `permission-service`, `config-service`,
`migration-service`

## Entscheidung

`permission-service` prüfte bislang bei zwei Gruppen eigener, sicherheitsrelevanter Endpunkte
überhaupt keine Berechtigung:

1. **`POST /roles` und `PUT /roles/{id}`** — jeder beliebige, nicht authentifizierte Aufrufer konnte
   bislang neue Rollen mit beliebigen Permissions anlegen oder bestehende umbenennen/umkonfigurieren.
2. **`POST /scope-locks` und `DELETE /scope-locks/{id}`** — bereits Vier-Augen-fähig (P6-S4), aber ohne
   RBAC-Vorprüfung: jeder konnte eine Bereichssperre auslösen/aufheben, unabhängig von der
   Vier-Augen-Konfiguration.

Beide bekommen jetzt eine echte `admin.user_management`-Prüfung:

- **`_require_role_management(session, x_dms_principal)`** (neuer Helfer in `main.py`) — `401` ohne
  `X-DMS-Principal`-Header, sonst `repository.require_capability(session, x_dms_principal,
  "admin.user_management")`, `403` bei Ablehnung. Genutzt von `create_role`/`update_role`.
- **Scope-Locks nutzen `payload.locked_by`/`payload.released_by`** statt eines Headers — beide Felder
  existierten bereits als Akteur-Quelle für die Vier-Augen-`initiated_by`-Logik, eine zweite,
  inkonsistente Identitätsquelle im selben Endpunkt wäre unnötig gewesen. Die Capability-Prüfung läuft
  **vor** dem bestehenden `get_approval_config`-Zweig, sonst könnte ein unberechtigter Aufrufer eine
  `pending_approval`-Anfrage auslösen, ohne die Basis-Capability überhaupt zu halten.
- **`GET /roles` bleibt bewusst ungegatet** — reine Lesbarkeit wird von `dms-permission-client`s
  `get_role_id` und diversen anderen Services vorausgesetzt (Rollen-Get-or-Create per Name).
- **`POST /role-assignments` bleibt bewusst ungegatet** — siehe "Begründung" unten (ADR 0023).

**Drei reale Konsumenten mussten nachgezogen werden**, da sie zwar die nötige Capability an ihrem
eigenen Bootstrap bereits hielten, aber (anders als ihre Geschwister-Clients) bislang keinen
`X-DMS-Principal`-Header sendeten:

- `config-service`s `PermissionServiceClient` (`clients.py`) — Header `config-service` ergänzt. Kein
  Bootstrap-Zusatz nötig: `_REQUIRED_ROLE_NAMES` enthielt `"domain-admin-users"` bereits seit P17-S1.
- `migration-service`s `LocalDmsClient` (`dms_client.py`) — Header `migration-service` ergänzt (wirkt
  auf beide Scope-Lock-Methoden UND `apply_role_assignment`s Rollen-Get-or-Create).
- `migration-service`s eigener Bootstrap (`main.py::_ensure_config_admin_permission`) — bislang nur
  `"domain-admin-config"` (`admin.object_config`), um `"domain-admin-users"`
  (`admin.user_management`) erweitert.

## Begründung

- **Warum `admin.user_management` statt einer neuen Capability**: `config-service` hält sie bereits
  (kein neuer Grant nötig), und semantisch ist Rollen-/Bereichssperrenverwaltung "Nutzer-/
  Rechteverwaltung" — dieselbe Wahl wie `auth-service`s `_require_service_user_management` für
  Realm-Rollen.
- **Warum `POST /role-assignments` NICHT gegated wird**: ADR 0023s Henne-Ei-Fall betrifft explizit
  diesen Endpunkt — `auth-service`s Bootstrap legt darüber die allererste Rollenzuweisung für
  `users-admin` an, die selbst noch keine Berechtigung hält (Phase 18s technische Konten lösen dieses
  Problem für Login/Break-Glass, nicht für diesen einen Bootstrap-Schritt). Recherche vor dieser Session
  bestätigte: Rollen-*Anlage* selbst (`POST /roles`) hängt bei KEINEM Service an einem
  HTTP-Bootstrap-Pfad — jeder Service legt seine Startrollen direkt gegen `repository` an, nie über
  die eigene HTTP-API. Das Henne-Ei-Problem existiert für `/roles` also nicht, weshalb diese Session es
  gefahrlos gaten kann, ohne ADR 0023s Ausnahme zu berühren.
- **Warum Scope-Locks über Body-Feld statt Header**: `ScopeLockCreate.locked_by`/
  `ScopeLockRelease.released_by` sind bereits die etablierte Akteur-Quelle in diesen zwei Endpunkten
  (Vier-Augen-`initiated_by`) — ein zusätzlicher, unabhängiger `X-DMS-Principal`-Header hätte zwei
  konkurrierende Identitätsquellen im selben Request geschaffen.
- **Bekannte, akzeptierte Konsequenz**: `permission-service` hat (anders als z. B. `document-service`)
  **keinen generischen Superuser-Bypass** für `require_capability` — nur `POST
  /maintenance-mode/lift` hat eine hartkodierte Superuser-Sonderprüfung. Ein Break-Glass-Superuser ohne
  explizite `admin.user_management`-Zuweisung könnte also nach dieser Session keine Rollen mehr anlegen
  oder Bereichssperren setzen. Das ist eine größere, architektonische Änderung außerhalb dieses
  Sessionsumfangs — dokumentiert, nicht behoben.

## Konsequenzen

- **Tests**: `permission-service` 128 (vorher ~122, threading der neuen `role_management_headers`-
  Fixture durch `test_api.py` plus zwei neue negative Tests, `_grant_permission_via_api`-Helfer um einen
  `headers`-Parameter erweitert; zusätzlich zwei Tests in `test_scope_lock_events.py` mit einer
  lokalen `_grant_scope_lock_permission`-Hilfsfunktion nachgezogen). `config-service` 48, `migration-
  service` 8 — beide unverändert grün nach dem Header-Fix. `ruff check`/`ruff format --check` clean für
  alle drei Services.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau aller drei
  Services + Neustart): `POST /roles` ohne Header → `401`; mit falschem Principal → `403`; mit
  `X-DMS-Principal: config-service` → `200`. `POST /scope-locks` mit leerem `locked_by` → `403`.
  `migration-service`s eigener Scope-Lock-Erwerb/-Freigabe-Zyklus (`locked_by="migration-service"` samt
  `X-DMS-Principal`-Header aus dem Client-Konstruktor) → erfolgreich. Beide Services' Bootstrap-Logs
  zeigen keine `*_role_missing`-Warnung, beide halten `domain-admin-users` laut `GET
  /role-assignments?principal_id=...` nach dem Neustart.
- **Ein Datenbereinigungsfund während der Live-Verifikation**: `permission.role_assignment.create` stand
  in der laufenden Entwicklungsdatenbank noch von einer früheren, manuellen Live-Verifikation auf
  `requires_approval=true` — brach `config-service`s eigene `authorized_principal`-Testfixture (die eine
  *sofortige* Rollenzuweisung erwartet). Kein Zusammenhang mit dieser Session, aber blockierte deren
  Testlauf gegen den echten Stack — zurückgesetzt auf `false`.
- **Kein genereller Superuser-Bypass für `require_capability`** (siehe "Begründung") — bleibt offen,
  vermerkt in `docs/services/permission-service.md` "Offene Punkte".
