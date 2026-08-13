# auth-service

**Verantwortung:** Schlanker OIDC-Broker vor Keycloak — hält Client-Secret und Admin-Zugang, Aufrufer sehen nur Login/Refresh/Token-Validierung (Konzept 4.4). Keine eigene IAM-Logik, keine eigene Nutzertabelle.

**Konzept-Referenz:** 4.4/2.5 (Kontakte, seit P15-S4)/7.4 (föderierte Kontaktsuche, seit P15-S4)/14.1 (Realm-Rollen für Konfigurationspakete, seit P17-S1)
**Eigenes Postgres-Schema:** `auth` (seit P15-S4, `federation_identity` — eine Singleton-Zeile für die optionale föderierte Kontaktsuche; seit dem Ad-hoc-Post-Roadmap-SSO-Feature zusätzlich `sso_config`, ebenfalls eine Singleton-Zeile; seit Phase 18 zusätzlich `local_signing_key` (Singleton) und `technical_account`, siehe "Auth-Entkopplung von Keycloak" unten). Bis P15-S4 war der Service vollständig zustandslos; Keycloak selbst verwaltet seine Daten weiterhin im eigenen Schema `keycloak` (siehe `infra/postgres-init/001-schemas.sql`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/login` | `{username, password}` → Password-Grant gegen Keycloak, liefert Access-/Refresh-Token. **Seit P6-S6**: liest `X-DMS-Maintenance-Active` (vom Gateway injiziert, 4.8) — ist Wartungsmodus aktiv und `username` ungleich dem Superuser-Konto, `503` statt Login. **Seit Phase 18 Session 2/3**: erkennt technische Konten (`technical_account`-Tabellen-Lookup) vor dem Keycloak-Pfad und authentifiziert diese lokal (bcrypt) — seit Session 2 der Superuser, seit Session 3 zusätzlich beide Domain-Admin-Konten, siehe "Auth-Entkopplung von Keycloak" unten |
| `POST` | `/refresh` | `{refresh_token}` → neue Tokens. **Seit Phase 18 Session 2**: erkennt lokal ausgestellte Refresh-Tokens am `iss`-Claim und stellt ein frisches Paar ohne Keycloak-Beteiligung aus |
| `GET` | `/me` | Bearer-Token validieren (JWKS, zustandslos, keine Rückfrage bei Keycloak), normalisierte Identität zurückgeben |
| `GET` | `/users` | Nutzer auflisten (seit P4-S3, Grundlage der Admin-UI-Nutzerverwaltung) — liest direkt aus Keycloak. **Seit P6-S5 gegated**: erfordert die Capability `admin.user_management` (Domäne "Nutzer-/Rechteverwaltung", 4.6), sonst `403` |
| `POST` | `/users` | Nutzer anlegen (`username`, `email`, `password`, `first_name`, `last_name`) — 409 bei bereits vergebenem Benutzernamen. Gegated wie `GET /users` |
| `DELETE` | `/users/{id}` | Nutzer löschen — 404 bei unbekannter `id`. Gegated wie `GET /users` |
| `GET` | `/me/preferences` | Theme-Präferenz des angemeldeten Kontos (`{theme}`, Default `"auto"`) — seit P4-S6 |
| `PUT` | `/me/preferences` | Theme-Präferenz setzen (`{theme}` ∈ `light`/`dark`/`high-contrast`/`auto`, sonst 422) — seit P4-S6 |
| `GET` | `/superuser/status` | Break-Glass-Status (4.6, seit P6-S5): `{active, expires_at}`, seit **P6-S6** zusätzlich `principal_id` (seit Phase 18 Session 2 die `TechnicalAccount.id`, zuvor die Keycloak-`id`, für den Not-Shutdown-Aufheben-Check des Permission Service, 4.8) — 404, falls das Superuser-Konto noch nicht angelegt wurde |
| `POST` | `/superuser/deactivate` | Vorzeitige, freiwillige Deaktivierung (seit P6-S5) — ergänzt die automatische Ablauf-Erzwingung über den Poll-Loop |
| `GET` | `/users/lookup` | Exakte Namensauflösung (`?username=`) — liefert nur `{id, username}` zurück, `404` bei unbekanntem Namen. Neu in P14-S6, für `teamspace-service`s Einladen-per-Nutzername (2.5): bewusst NICHT hinter `admin.user_management` wie `GET /users` oben — jede Person, nicht nur Domain-Admins, soll andere zu einem Team-Arbeitsbereich einladen können. Seit P19-S3 (ADR 0068) über die "everyone"-Gruppe aus permission-service gegated (`users.lookup`, seit P19-S2 vorgeseedet) statt nur `Depends(get_current_user)` — am Ist-Verhalten ändert sich nichts, die Berechtigung ist aber jetzt admin-editierbar. Siehe [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md) |
| `GET` | `/users/count` | Interner Aufruf von `license-service` (9.1 "benannte Accounts"-Modell, seit P9-S1) — ungegatet, da kein Service einen echten Keycloak-Bearer-Token für `Depends(get_current_user)` besitzt |
| `GET` | `/sessions/count` | Interner Aufruf von `license-service` (9.1 "gleichzeitige Nutzer"-Modell, seit P9-S1) — `KeycloakAdmin.get_client_sessions_stats()`, ungegatet |
| `GET` | `/users/directory?q=` | Verzeichnissuche (2.5/4.4, seit P15-S4, Keycloak-`search`-Parameter — Präfix je Feld, kein Teilstring, siehe "Kontakte" unten) — kein `admin.user_management`-Gate, seit P19-S3 (ADR 0068) aber über die "everyone"-Gruppe aus permission-service geprüft (`users.directory`) statt nur authentifiziert sein zu müssen |
| `GET` | `/users/directory/federation-status` | Ob die föderierte Kontaktsuche auf dieser Installation aktiviert ist (`{enabled, peer_installation_count}`) — ungegatet, steuert die Sichtbarkeit der entsprechenden Frontend-Sektion |
| `GET` | `/users/directory/federated?q=` | Föderierte Suche über alle bekannten, für Kontaktsuche freigegebenen Peer-Installationen (2.5/7.4, seit P15-S4) — `403`, falls auf dieser Installation nicht aktiviert |
| `POST` | `/users/directory/federated-search-inbound` | Von einer Peer-Installation aufgerufen (öffentliche Route, kein `X-DMS-Principal`) — authentisiert über `X-Installation-Signature`/`X-Installation-Id`, siehe "Kontakte" unten |
| `GET` | `/realm-roles` | **Seit P17-S1** (14.1): aktuelle Keycloak-Realm-Rollen, gefiltert um Keycloak-Built-ins (`offline_access`, `uma_authorization`, `default-roles-*`) — ungegatet, liefert nur Namen (identisches Vertrauensmodell wie `permission-service`s `GET /roles`) |
| `POST` | `/realm-roles` | **Seit P17-S1**: legt die übergebenen Realm-Rollen idempotent an (`{names: [...]}`, `create_realm_role(..., skip_exists=True)`) — verlangt `X-DMS-Principal`-Header mit `admin.user_management`-Berechtigung (Service-zu-Service, kein Keycloak-JWT-Endpunkt), sonst `403`. Weist die Rolle niemandem zu, siehe "Realm-Rollen-Verwaltung" unten |
| `GET` | `/healthz` | Eigener Health-Check |
| `GET` | `/.well-known/jwks.json` | **Seit Phase 18** (ADR 0063): öffentlicher Schlüssel für Tokens lokaler technischer Konten, gleiches Format wie Keycloaks JWKS. Ungegatet |
| `GET` | `/oidc/authorize?redirect_uri=&state=` | **Ad-hoc Post-Roadmap** (SSO, siehe ADR 0062): prüft `redirect_uri` gegen `sso_redirect_uri_allowed_origins` (400 sonst, Open-Redirect-Absicherung), liefert `{authorization_url}` — der Client navigiert selbst dorthin. Öffentlich (Login-Einstiegspunkt) |
| `POST` | `/oidc/callback` | `{code, redirect_uri}` → tauscht den Code serverseitig gegen Tokens, liefert dieselbe `TokenResponse`-Form wie `/login`. Prüft Not-Shutdown ERST NACH dem Austausch (Benutzername vorher unbekannt) — siehe ADR 0062. Öffentlich |
| `GET` | `/sso-config` | `{enabled, updated_at}` — ob SSO installationsweit aktiv ist. Ungegatet, `login/page.tsx` fragt dies vor dem Formular ab |
| `PUT` | `/sso-config` | `{enabled}` setzen — gegated auf `admin.user_management`, gleiche Domäne wie Nutzerverwaltung |
| `POST` | `/logout` | `{refresh_token}` → beendet die Sitzung wirklich auf Keycloak-Seite (`.../protocol/openid-connect/logout`) — vorher gab es keinen serverseitigen Logout-Mechanismus |

## Realm-/Client-Bootstrap

Bei jedem Start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential Client `dms-api` mit `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (kein Browser-Redirect-Flow in dieser Session)
- Audience-Mapper, damit `aud` im Access-Token `dms-api` statt nur `account` enthält (Keycloak-Default ohne Mapper)
- Deklariertes User-Profile-Attribut `dms_theme` (seit P4-S6, siehe unten) — ohne diese Deklaration verwirft Keycloaks Declarative User Profile das Attribut bei jedem `update_user`-Aufruf stillschweigend
- Realm-Rolle `dms-admin` (seit **P5e-S2**, `create_realm_role(..., skip_exists=True)`) — erste im System tatsächlich ausgewertete Rolle, siehe `docs/services/document-service.md` "Kennzeichengenerator" (privilegierte Änderung von `attributes["Kennzeichen"]`)
- ~~Deklariertes User-Profile-Attribut `dms_superuser_expires_at`~~ / ~~Superuser-Konto hier angelegt~~ — **seit Phase 18 Session 2 entfernt** ([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)): der Superuser lebt nicht mehr in Keycloak, seine idempotente Anlage passiert jetzt async in `main.py`s Lifespan (`superuser.ensure_superuser_account`, DB-basiert), nicht mehr hier in diesem synchronen, rein Keycloak-fokussierten Bootstrap-Schritt.
- ~~Technische Domain-Admin-Konten hier angelegt~~ — **seit Phase 18 Session 3 entfernt**
  ([ADR 0065](../adr/0065-domain-admin-migration-lokale-technische-konten.md)): `users-admin`/
  `config-admin` leben nicht mehr in Keycloak, ihre idempotente Anlage passiert jetzt async in
  `main.py`s Lifespan (`domain_admins.ensure_domain_admin_account`, DB-basiert), direkt neben dem
  Superuser, nicht mehr hier in diesem synchronen, rein Keycloak-fokussierten Bootstrap-Schritt.
- **Seit Ad-hoc-Post-Roadmap-SSO-Feature**: `_ensure_client_updated` (läuft bei JEDEM Start, nicht nur bei Ersteinrichtung) aktiviert `standardFlowEnabled` und registriert die Redirect-URIs (`{origin}/login/callback/` je `sso_redirect_uri_allowed_origins`) — behebt die unten genannte `skip_exists`-Lücke für genau diese beiden Felder. `_ensure_kerberos` (bedingt, nur wenn `kerberos_enabled` und alle drei Kerberos-Settings gesetzt sind) richtet zusätzlich Kerberos/SPNEGO ein, siehe "SSO/automatischer Login" unten und [ADR 0062](../adr/0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md).

**Bekannte Grenze**: `skip_exists=True` verhindert weiterhin, dass eine spätere Änderung der übrigen Client-Konfiguration (z. B. neue Mapper) auf einen bereits bestehenden Client nachgezogen wird — für Dev/Test unkritisch, für Produktivbetrieb bei Konfigurationsänderungen zu beachten. Nur `standardFlowEnabled`/`redirectUris` sind seit dem SSO-Feature davon ausgenommen (siehe oben).

## Theme-Präferenz (Konzept 8, seit P4-S6)

Cross-UI-Theming (Hell/Dunkel/Hoher-Kontrast/Automatisch, User-UI und Admin-UI) speichert seine Präferenz geräteübergreifend am Nutzerkonto statt nur lokal im Browser — Begründung und Stolpersteine (Declarative-User-Profile-Falle) in [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Kurzfassung: `dms_theme` ist ein deklariertes Keycloak-Nutzerattribut, gelesen/geschrieben über den bestehenden Admin-Client (`admin_users.get_theme_preference`/`set_theme_preference`), exponiert über `/me/preferences`. Kein neuer Persistenz-Baustein nötig.

## Domänengetrennte Admin-Rollen (4.6, seit P6-S5)

Domain-Admin-"Rollen" sind bewusst **keine Keycloak-Realm-Rollen** (anders als `dms-admin`), sondern systemeigene `Role`-Zeilen in `permission-service` (siehe `docs/services/permission-service.md`) — `auth-service` erzeugt nur die zugehörigen **technischen Konten** und weist ihnen die Rolle per HTTP-Aufruf gegen `permission-service` zu (`permission_client.py`, `PermissionServiceClient.ensure_role_assignment`). Vollständige Architekturbegründung siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Aktuell tatsächlich angelegt: `users-admin` (Domäne "Nutzer-/Rechteverwaltung") und `config-admin` (Domäne "Workflow-Konfiguration", seit P6-S6) — **seit Phase 18 Session 3 als `TechnicalAccount`-Zeilen statt Keycloak-Konten** ([ADR 0065](../adr/0065-domain-admin-migration-lokale-technische-konten.md)), siehe "Auth-Entkopplung von Keycloak" unten. Die Rollenzuweisung erfolgt weiterhin best-effort beim Lifespan-Start — ist `permission-service` noch nicht erreichbar (oder die Zuweisung auf dieser Installation Vier-Augen-pflichtig und noch nicht genehmigt), wird sie übersprungen und beim nächsten Neustart erneut versucht (kein Retry-Loop).

## Superuser Break-Glass (4.6, seit P6-S5, seit Phase 18 Session 2 lokal statt Keycloak)

Ein einzelnes, standardmäßig deaktiviertes (`enabled=False`) Konto `superuser`. **Seit Phase 18 Session 2**
([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)) eine `TechnicalAccount`-
Zeile im eigenen `auth`-Schema statt eines Keycloak-Nutzerkontos — Break-Glass funktioniert dadurch
unabhängig von Keycloaks Erreichbarkeit, der eigentliche Zweck eines Notfallmechanismus. Reaktivierung
läuft weiterhin **ausschließlich** über den generischen Vier-Augen-Mechanismus des Permission Service
(P6-S4, ADR 0022): `POST /approval-requests` mit `action_type="auth.superuser.activate"` gegen
`permission-service`, das für diesen Aktionstyp beim eigenen Start `requires_approval=True` und
`required_permission="breakglass.approve"` vorbelegt (strenger als die "irgendeine zweite Person"-Regel
aus 4.3 — Initiator *und* Genehmiger müssen die Rolle `breakglass-approver` halten). Nach Genehmigung
konsumiert `auth-service` (**erster NATS-Konsument dieses Service überhaupt**, `consumer.py`) das
publizierte `permission.approval.approved` und aktiviert das Konto: `enabled=True` +
`expires_at`-Spalte (`activated_at + superuser_activation_minutes`, Default 30 min, jetzt eine echte
DB-Spalte statt eines Keycloak-Attributs) — publiziert danach `auth.superuser.activated`.

Ein periodischer Poll-Loop (`_superuser_poll_loop`, `superuser_poll_interval_seconds`, Default 30s — exakt dasselbe Muster wie workflow-services SLA-Zeitüberwachung, [ADR 0020](../adr/0020-sla-timer-polling.md)) deaktiviert abgelaufene Aktivierungen automatisch und publiziert `auth.superuser.deactivated` (`reason="expired"`, oder `"manual"` bei `POST /superuser/deactivate`). **Bewusste Vereinfachung** (siehe ADR 0023): ein einziger absoluter Ablauf-Zeitstempel statt separater Gesamtdauer- und rollierender 10-Minuten-Inaktivitäts-Timer.

`POST /login` erkennt den Superuser-Benutzernamen über einen `technical_account`-Tabellen-Lookup und
authentifiziert lokal (bcrypt-Passwortprüfung + `enabled`/`expires_at`-Prüfung), statt einen
Keycloak-Password-Grant zu versuchen — der seit P6-S6 bekannte, nie behobene Bug ("Superuser-Konto kann
nicht interaktiv einloggen", fehlende Pflichtfelder an einem historisch unvollständig angelegten
Keycloak-Konto) ist damit ersatzlos verschwunden, es gibt kein Keycloak-Konto mehr, das diesen Zustand
haben könnte.

## Not-Shutdown (4.8, seit P6-S6)

`POST /login` liest den vom Gateway auf jedem proxied Request injizierten `X-DMS-Maintenance-Active`-Header (Default `"false"`, falls das Login direkt am Service statt über das Gateway aufgerufen wird — dann ist der Wartungsmodus faktisch nie wirksam, siehe `docs/services/gateway-service.md`): ist er `"true"` und der angefragte `username` ungleich `superuser.SUPERUSER_USERNAME`, wird der Login mit `503` abgelehnt, **bevor** überhaupt ein Password-Grant gegen Keycloak versucht wird — wörtliche Umsetzung von "neue Logins außer für den Superuser werden abgelehnt" (4.8). Der Superuser-Login selbst wird dadurch nicht automatisch erfolgreich — ein falsches Passwort liefert weiterhin `401`, der Header entscheidet nur, ob überhaupt versucht wird. Vollständige Architekturbegründung (Gateway als Durchsetzungspunkt, Header-Broadcast-Muster) in [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

## Auth-Entkopplung von Keycloak (Post-Roadmap Phase 18, siehe ADR 0063/0064/0065)

Superuser-Break-Glass und Domain-Admin-Konten (`users-admin`/`config-admin`) funktionieren seit
Phase 18 vollständig unabhängig von Keycloaks Erreichbarkeit (Nutzer-Direktive: "der Superuser soll gar
nicht im Keycloak leben, das Selbe gilt für die Domänen-Admins").

- **`TechnicalAccount`** (Model, `auth`-Schema) — Speicherort für Superuser-/Domain-Admin-Konten,
  `password_hash` per `bcrypt` (erstes selbst gehashtes Passwort in diesem Service). `role_name`
  nullable — `NULL` für den Superuser (Sonderrechte laufen über direkten Namensvergleich, nicht RBAC),
  gesetzt (`domain-admin-users`/`domain-admin-config`) für die beiden Domain-Admin-Konten
  (`domain_admins.py`, seit Session 3, strukturell fast identisch zu `superuser.py`: `enabled=True`
  sofort statt Break-Glass, sonst gleiches idempotentes Anlage-Muster).
- **`LocalSigningKey`** (Singleton-Zeile, gleiches Muster wie `FederationIdentity`) — eigenes
  RSA-2048-Schlüsselpaar, idempotent beim ersten Zugriff erzeugt, stabiler `kid` über Neustarts hinweg.
- **`GET /.well-known/jwks.json`** — liefert den öffentlichen Schlüssel im selben JWKS-Format wie
  Keycloaks `/protocol/openid-connect/certs`, ungegatet.
- **`local_token_issuer.mint_token()`** — stellt Tokens mit identischem Claim-Shape wie Keycloak aus
  (`sub`/`preferred_username`/`realm_access.roles`/`aud`), plus `jti` (verhindert byte-identische Tokens
  bei zwei Ausstellungen für dasselbe Konto innerhalb derselben Sekunde, z. B. Login direkt gefolgt von
  Refresh — real bei der Testentwicklung aufgetreten).
- **`_validator` ist ein `MultiIssuerTokenValidator`** (neu in `libs/dms-auth-client`, wählt über den
  `iss`-Claim) aus Keycloak- und lokalem Validator — vollständig additiv, bestehende Keycloak-Logins
  bleiben unverändert gültig. Ein `_LazyValidator`-Wrapper verzögert den Zugriff auf
  `app.state.combined_validator` bis zum ersten Request, da der lokale Signierschlüssel erst nach einem
  DB-Zugriff im Lifespan verfügbar ist.
- **`POST /login`/`POST /refresh` erkennen technische Konten**: `/login` prüft den Benutzernamen gegen
  `technical_account`, bevor ein Keycloak-Password-Grant versucht wird; `/refresh` peekt den `iss`-Claim
  des präsentierten Refresh-Tokens (`local_token_issuer.is_local_token`) und verzweigt entsprechend.
  Beide Pfade liefern dieselbe `TokenResponse`-Form, unabhängig von der Quelle.
- **`gateway-service`s eigener `TokenValidator` ist ebenfalls ein `MultiIssuerTokenValidator`** (neue
  `DMS_AUTH_SERVICE_BASE_URL`-Einstellung für die zweite JWKS-Quelle) — ohne diese Umstellung würde ein
  frisch lokal eingeloggter Superuser an jedem proxied Aufruf mit 401 scheitern, obwohl `auth-service`
  sein eigenes Token korrekt validiert.

**Vollständig live gegen den echten laufenden Stack verifiziert** (nicht nur automatisierte Tests, Session
2, Superuser): kompletter Kreislauf über das echte Gateway — Login vor Aktivierung (401) → Aktivierung →
Login über das Gateway (200) → `GET /me` über das Gateway (200, beweist Gateways eigene
Multi-Issuer-Umstellung) → ein Aufruf gegen `document-service` mit demselben Token über das Gateway wird
durchgelassen (422 wegen eines fachlichen Pflichtfelds, nicht 401 — beweist systemweite Akzeptanz) →
`POST /refresh` über das Gateway (200) → Deaktivierung → nachfolgender Refresh (401).

**Session 3 (Domain-Admins) ebenfalls live verifiziert**: nach Neubau des `auth-service`-Images zeigte
der `iss`-Claim frisch ausgestellter `users-admin`-/`config-admin`-Tokens `dms-auth-service-local` statt
der Keycloak-Realm-URL — beide Konten sind damit tatsächlich lokal, nicht mehr über Keycloak. `GET /users`
mit `users-admin`-Token über das Gateway → 200, `GET /me` mit `config-admin`-Token über das Gateway → 200
mit `realm_roles: ["domain-admin-config"]`. Die alten Keycloak-Konten für beide Benutzernamen bleiben als
ungenutzte Karteileichen bestehen (`/login` findet das `TechnicalAccount` zuerst und erreicht den
Keycloak-Fallback nie mehr) — kein automatisiertes Aufräumen, siehe ADR 0065 "Konsequenzen".

## SSO/automatischer Login (Ad-hoc Post-Roadmap-Feature, siehe ADR 0062)

Optional, installationsweit aktivierbar über `GET/PUT /sso-config` (Singleton-Zeile, gleiches Muster wie `document-service`s `ShareLinkConfig`). Ist SSO aktiv, leitet `user-ui`s `login/page.tsx` VOR dem Anzeigen des Passwort-Formulars zu Keycloaks eigener Login-Seite um (`GET /oidc/authorize`, Antwort enthält nur die URL, der Client navigiert selbst dorthin). Besitzt der Rechner ein gültiges Kerberos-Ticket UND ist Kerberos konfiguriert (siehe unten), meldet Keycloaks SPNEGO-Mechanismus automatisch an; andernfalls zeigt Keycloak selbst sein gehostetes Formular — reiner Fallback, kein Bruch. `POST /oidc/callback` tauscht den Code serverseitig gegen Tokens (`dms-api` ist confidential, kein PKCE nötig, nur `state` als CSRF-/Replay-Schutz) und liefert dieselbe `TokenResponse`-Form wie `/login`.

**Kerberos/SPNEGO** (`_ensure_kerberos`, bedingt auf `kerberos_enabled`+`kerberos_realm`+`kerberos_server_principal`+`kerberos_keytab_path`): dupliziert Keycloaks eingebauten `browser`-Flow (bringt eine standardmäßig deaktivierte `auth-spnego`-Ausführung bereits mit) zu `dms-browser-kerberos`, aktiviert die SPNEGO-Ausführung (`requirement=ALTERNATIVE`), verweist den Realm per `browserFlow` darauf und legt eine Kerberos-User-Federation-Komponente an.

**`POST /logout`** ist neu — vorher gab es keinen serverseitigen Session-Abbau, "Abmelden" löschte nur lokale Tokens. Ruft Keycloaks `.../protocol/openid-connect/logout` mit dem Refresh-Token auf, ohne das würde ein SPNEGO-fähiger Browser sich beim nächsten Besuch sofort wieder automatisch anmelden.

**Nicht in dieser Sandbox live verifizierbar**: das eigentliche automatische Einloggen über ein echtes Kerberos-Ticket (kein Domain-Controller/KDC vorhanden) — dokumentierte, mit dem Nutzer abgestimmte Grenze. Vollständig verifizierbar und getestet: Bootstrap-Idempotenz, sauberes Überspringen ohne Kerberos-Konfiguration, der komplette Redirect+Callback-Fluss über Keycloaks eigenes Formular sowie `/logout`.

## Events

**Publiziert** (`stream="auth"`, seit P6-S5): `auth.superuser.activated` (`{request_id, expires_at}`), `auth.superuser.deactivated` (`{reason}`, `"expired"`|`"manual"`).

**Konsumiert** (`durable="auth-service"`, seit P6-S5, erster Konsument dieses Service): `permission.approval.approved`, gefiltert auf `action_type="auth.superuser.activate"` — jeder andere Aktionstyp wird ignoriert (gehört einem anderen Service, gleiches Prinzip wie in ADR 0022 beschrieben).

## Kontakte (2.5/4.4/7.4, seit P15-S4)

Verzeichnis zum Auffinden anderer Mitarbeitender - "lokal, immer verfügbar" (2.5, wörtlich), optional installationsübergreifend. Vollständige Architekturbegründung: [ADR 0054](../adr/0054-kontakte-directory-independent-second-federation-identity-per-installation.md).

- **Lokale Suche**: `admin_users.search_users` nutzt Keycloaks eingebauten `search`-Query-Parameter (case-insensitive über Benutzername/Vor-/Nachname/E-Mail, serverseitig in Keycloak selbst) — kein eigener Filter-Mechanismus nötig. Antwort bewusst ohne `enabled`-Feld (Freigabestatus ist eine administrative Angelegenheit). **Per Live-Verifikation korrigiert** (ursprünglich als "Teilstring" angenommen): Keycloaks `search` matcht je Feld nur als **Präfix**, nicht an beliebiger Stelle — `q=admin` findet `config-admin` nicht, `q=config` schon (siehe ADR 0054 "Offene Punkte").
- **Föderierte Suche - eigene, zweite Federation-Hub-Identität**: `auth-service` registriert sich unabhängig von `workflow-service`s bereits bestehender Federation-Teilnahme (P6-S9/P13-S4) ein zweites Mal beim selben Hub (eigenes RSA-2048-Schlüsselpaar, eigene `installation_id`, Anzeigename-Suffix `" (Kontakte)"`) — `_ensure_federation_identity()` in `main.py`, identisches Muster wie `workflow_service.main._ensure_federation_identity`. Opt-in über `DMS_FEDERATION_HUB_BASE_URL`; zusätzlich `DMS_FEDERATED_DIRECTORY_ENABLED` muss `true` sein, damit die föderierten Endpunkte tatsächlich aktiv sind (zwei getrennte Schalter: Hub-Registrierung vs. tatsächliche Freigabe für Suchanfragen).
- **Fähigkeits-Markierung**: registriert sich mit `supported_process_types=["dms.contact-directory.v1"]` — zweckentfremdet das bereits bestehende, generische Listenfeld in `federation-hub-service`s `Installation`-Modell als Fähigkeits-Marker (`directory_federation.CONTACT_DIRECTORY_CAPABILITY`), keine Code-Änderung in federation-hub-service nötig.
- **Direkte Installation-zu-Installation-Anfragen, nicht über den Hub relayt**: `GET /users/directory/federated` fragt jede über `GET /installations` bekannte, nicht widerrufene, für Kontaktsuche freigegebene Peer-Installation DIREKT über deren `callback_base_url` an (`directory_federation.search_all_peers`/`query_peer`), signiert mit dem eigenen privaten Schlüssel (`X-Installation-Signature`, RSA-PSS/SHA-256, identisches Schema wie ADR 0039 — `federation_crypto.py`, dupliziert wie bereits zweimal zuvor in diesem Projekt, siehe ADR 0054). Ein einzelner nicht erreichbarer/ablehnender Peer blockiert die übrigen nicht.
- **`POST /users/directory/federated-search-inbound`**: empfängt eine signierte Anfrage einer Peer-Installation — verifiziert `X-Installation-Signature` gegen den beim Hub hinterlegten öffentlichen Schlüssel der ANFRAGENDEN Installation (live per `GET /installations` abgerufen, kein lokaler Peer-Schlüsselspeicher), lehnt unbekannte/widerrufene/nicht für Kontaktsuche registrierte Installationen mit `401` ab. Öffentliche Route am Gateway (`gateway-service`s `public_routes`, kein `X-DMS-Principal`), analog zu `workflow-service`s `federation/inbound`.
- **Keine Ende-zu-Ende-Verschlüsselung der Nutzlast** (anders als das Handover-Schema) — der Hub liegt bei direkten Aufrufen ohnehin nie im Anfragepfad, siehe ADR 0054 "Begründung".

## Realm-Rollen-Verwaltung (14.1, seit P17-S1)

Bis P17-S1 wurde jede Keycloak-Realm-Rolle einzeln, hart codiert im Bootstrap angelegt
(`bootstrap._ensure_dms_admin_role` für `dms-admin`) — kein genereller Weg, eine NEUE Realm-Rolle
anzulegen, ohne Code zu ändern und den Service neu zu deployen. `GET`/`POST /realm-roles`
verallgemeinert exakt dasselbe Primitiv (`create_realm_role(..., skip_exists=True)`) auf beliebige
Namen, damit ein Konfigurationspaket (`config-service`s neue `realm_roles`-Kategorie, z. B. für
`dms-poststelle`, 2.5) sie mitbringen kann, ohne einen neuen Mechanismus zu erfinden. **Bewusste
Grenze, identisch zum bestehenden `dms-admin`-Muster**: der Endpunkt legt nur die Rolle an, weist
sie niemandem zu — Zuweisung an konkrete Nutzer bleibt weiterhin außerhalb dieses Service über die
Keycloak Admin Console (siehe "Offene Punkte"). Details/Begründung siehe
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/auth-service/tests` (**64 Tests**, davon 5 neu seit **P17-S1**,
`test_realm_roles.py`: `GET /realm-roles` enthält das bereits bootstrapped `dms-admin`, schließt
Keycloak-Built-ins aus, `POST /realm-roles` ohne/mit unautorisiertem Principal → `403`, legt eine
neue Rolle idempotent an — zweiter Aufruf mit demselben Namen scheitert nicht, gleiches
`authorized_principal`-Fixture-Muster wie `config-service`s `tests/conftest.py`. Davon 11 seit
**P15-S4**, `test_directory.py`): lokale Verzeichnis-Suche (Authentifizierungspflicht, Präfix-Treffer je Feld, für reguläre Nutzer verfügbar nicht nur Domain-Admins), Federation-Status-Default, `403` auf den föderierten Endpunkten ohne Aktivierung, sowie eine echte Selbst-Registrierung gegen den laufenden `federation-hub-service` (`federation_enabled`-Fixture, monkeypatcht `settings` vor einem frischen `TestClient(app)`) inkl. echtem Signaturprüfungs-Pfad (gültige/ungültige Signatur, unbekannte Installation, eigene Installation wird aus den föderierten Ergebnissen ausgeschlossen) — läuft gegen echtes Postgres/Keycloak/`federation-hub-service`, keine Mocks. **Nebenbei gefundener und behobener Bug**: `FederationHubClient.register()` übermittelte `supported_process_types` ursprünglich gar nicht an den Hub — die eigene Fähigkeits-Markierung (`dms.contact-directory.v1`) wäre dadurch nie tatsächlich im Adressbuch sichtbar gewesen, jede eingehende föderierte Anfrage (auch eine legitime) wäre mit `401` abgelehnt worden. Erst durch den echten Live-Selbst-Loopback-Test sichtbar geworden, nicht durch reines Mocking. **Zusätzlicher Befund bei der Live-Verifikation gegen den laufenden Gateway**: Keycloaks `search`-Parameter ist entgegen der ursprünglichen Annahme kein Teilstring-, sondern ein Präfix-Match je Feld — Dokumentation entsprechend korrigiert (siehe oben, ADR 0054 "Offene Punkte"). Ebenfalls beobachtet: wiederholte `federation_enabled`-Testläufe hinterlassen echte, dauerhafte Registrierungen im geteilten `federation-hub-service`-Adressbuch (kein Aufräumen möglich ohne konfigurierten `hub_operator_key`) — siehe ADR 0054 "Offene Punkte".

## Offene Punkte

- **AD-Gruppe → interne Rolle Mapping** (Konzept 4.4): Keycloak deckt lokale + LDAP/AD-föderierte Nutzer bereits nativ ab, aber die konfigurierbare Mapping-Regelengine (AD-Gruppe → DMS-Rolle) ist nicht implementiert. Rollenzuweisung/-auswertung ist Aufgabe des Permission Service (4.1, P2-S2); `/me` liefert aktuell nur Keycloaks rohe `realm_access.roles`.
- **Issuer-Hostname-Konsistenz — teilweise gelöst seit dem Ad-hoc-Post-Roadmap-SSO-Feature**: Der Auth Service spricht Keycloak intern über `DMS_KEYCLOAK_BASE_URL` (im Compose-Netz `http://keycloak:8080`) an; ausgestellte Tokens tragen entsprechend `iss=http://keycloak:8080/realms/dms`. Mit dem neuen browserbasierten Redirect-Flow (`standardFlowEnabled`, seit dem SSO-Feature) wurde genau die hier vorhergesagte Konsequenz real: `GET /oidc/authorize` liefert eine URL, zu der der Browser navigiert — mit der internen `http://keycloak:8080` wäre das für den Browser nicht auflösbar gewesen. Behoben über eine neue, separate `keycloak_public_base_url`-Einstellung (`DMS_KEYCLOAK_PUBLIC_BASE_URL`, im Compose-Stack `http://localhost:8080`), die nur `_authorization_endpoint` (in `keycloak_client.py`) verwendet — Token-/Logout-Endpunkte bleiben auf der internen URL, da sie ausschließlich serverseitig aus `auth-service` heraus aufgerufen werden. `iss` im Token selbst bleibt weiterhin die interne URL (Keycloaks eigene `frontendUrl`-Konfiguration wäre der vollständige Fix dafür, hier bewusst nicht angefasst, da `TokenValidator` bereits konsistent gegen denselben internen Issuer prüft).
- **SAML 2.0** (Konzept 4.4, für ADFS-Alt-Föderationen) nicht Teil dieser Session.
- **`/users`-Endpunkte seit P6-S5 gegated** (siehe oben) — löst den vormaligen offenen Punkt für diesen Service. Die Zuweisung von `admin.user_management` an *weitere* Principals (z. B. echte Menschen zusätzlich zum technischen `users-admin`-Konto) läuft über die jetzt selbst gegatete Nutzer-/Rechteverwaltungs-Admin-UI-Seite (`POST /role-assignments` gegen `permission-service`).
- **Keine Rollenzuweisungs-API/-UI für Keycloak-Realm-Rollen** (seit P5e-S2, seit P17-S1 nur teilweise gelöst): `dms-admin`/`dms-poststelle` etc. sind Keycloak-Realm-Rollen, kein systemeigenes `permission-service`-Konstrukt (anders als die Domain-Admin-Rollen aus P6-S5). Seit P17-S1 existiert immerhin ein genereller **Anlege**-Weg (`POST /realm-roles`, z. B. aus einem Konfigurationspaket) — die **Zuweisung** an konkrete Nutzer bleibt aber weiterhin ausschließlich über die Keycloak Admin Console, keine API/UI dafür in diesem Projekt.
- **5 der 7 Domain-Admin-Rollen aus 4.6 ohne zugeordnetes technisches Konto** (seit P6-S5/S6): `domain-admin-storage`/`-license`/`-query-console`/`-deletion`/`-deletion-vs` existieren nur als `Role`-Zeile in `permission-service`, ohne Keycloak-Konto und ohne dass irgendein Endpunkt sie prüft — folgt jeweils mit der künftigen Retrofit-Session der betreffenden Domäne. `domain-admin-config` ist seit **P6-S6** durchgesetzt (`config-admin`-Konto, `workflow-service`s Prozessdefinitions-Endpunkte).
- **Keine erhöhte Auditierungspriorität während einer aktiven Superuser-Session** (4.6, seit P6-S5): `audit-service` konsumiert die Break-Glass-Lifecycle-Events (`auth.>`) mit normaler Priorität; Fremdaktionen, die *während* der Aktivierung in anderen Services ausgeführt werden, sind nicht gesondert markiert.
- **Keine rollierende Inaktivitäts-Deaktivierung** (4.6, seit P6-S5): ein einziger absoluter Ablauf-Zeitstempel statt getrennter Gesamtdauer-/10-Minuten-Inaktivitäts-Timer, siehe ADR 0023.
- ~~**Bug entdeckt bei P6-S6, nicht behoben (P6-S5-Code)**: das Superuser-Konto kann in der aktuellen Live-Umgebung nicht interaktiv einloggen (`POST /login` liefert `401`/"Account is not fully set up" direkt von Keycloak). Ursache: `firstName`/`lastName`/`email` fehlen am Keycloak-Konto...~~ — **seit Phase 18 Session 2 ersatzlos verschwunden** ([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)): der Superuser ist kein Keycloak-Konto mehr, es gibt kein Declarative-User-Profile-Pflichtfeld-Problem mehr, das diesen Zustand verursachen könnte.
- **`GET /users/lookup` ist ein Existenz-Oracle** (seit P14-S6): jeder authentifizierte Nutzer kann herausfinden, ob ein bestimmter Nutzername existiert — bewusst so belassen (interne Verwaltungssoftware, bekannte Nutzerpopulation), aber eine dokumentierte Abweichung vom vorherigen Zustand (Nutzerverzeichnis komplett hinter `admin.user_management`). Seit P19-S3 (ADR 0068) über die "everyone"-Gruppe gegated statt hartkodiert offen — ein Admin kann `users.lookup` der "everyone"-Rolle entziehen, um das Oracle zu schließen, ohne Code-Änderung. Siehe [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md).
