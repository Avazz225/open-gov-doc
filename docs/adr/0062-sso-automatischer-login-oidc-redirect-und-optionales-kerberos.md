# 0062 — SSO/automatischer Login: OIDC-Redirect-Flow mit optionalem Kerberos/SPNEGO als Erweiterung

**Status:** akzeptiert
**Kontext:** Ad-hoc Post-Roadmap-Feature (Nutzeranfrage nach Abschluss der 107-Session-Roadmap), betrifft
`auth-service`, `gateway-service`, `user-ui`

## Entscheidung

Optional, installationsweit aktivierbar (`GET/PUT /sso-config`, gleiches Singleton-Zeilen-Muster wie
`ShareLinkConfig`, gegated auf `admin.user_management`). Ist SSO aktiv, leitet `login/page.tsx` den
Browser VOR dem Anzeigen des Passwort-Formulars zu Keycloaks eigener Login-Seite um (`GET
/oidc/authorize`). Besitzt der Rechner ein gültiges Kerberos-Ticket UND ist Kerberos konfiguriert, meldet
Keycloaks SPNEGO-Mechanismus automatisch an, ohne dass ein Formular je sichtbar wird; andernfalls (der
Normalfall in dieser Sandbox) zeigt Keycloak selbst sein gehostetes Formular - kein Bruch, reiner
Fallback. Der Rückweg (`POST /oidc/callback`) tauscht den Code serverseitig gegen Tokens im bereits
bestehenden `TokenResponse`-Format, sodass sich am Frontend-Session-Handling nichts ändert.

Drei Bausteine:

1. **`_ensure_client_updated` (bootstrap.py, läuft bei JEDEM Start, nicht nur Ersteinrichtung)** - behebt
   eine bereits im Code selbst dokumentierte Lücke: `admin.create_client(..., skip_exists=True)`
   aktualisiert einen bereits existierenden Client nie. Aktiviert `standardFlowEnabled` und registriert
   die Redirect-URIs (`{origin}/login/callback/` je erlaubtem Origin,
   `sso_redirect_uri_allowed_origins`). Läuft UNABHÄNGIG von Kerberos - das ist der Teil, der den
   Redirect-zu-Keycloaks-eigenem-Formular-Fallback überhaupt erst ermöglicht.
2. **`_ensure_kerberos` (bootstrap.py, bedingt)** - nur wenn `kerberos_enabled` UND alle drei
   Kerberos-Einstellungen (`kerberos_realm`/`kerberos_server_principal`/`kerberos_keytab_path`) gesetzt
   sind. Dupliziert Keycloaks eingebauten `browser`-Flow (der bereits eine standardmäßig deaktivierte
   `auth-spnego`-Ausführung mitbringt) zu `dms-browser-kerberos`, setzt deren `requirement` auf
   `ALTERNATIVE`, verweist den Realm per `browserFlow` darauf und legt eine Kerberos-User-Federation-
   Komponente an (`config`-Werte als Einzelelement-Listen - Keycloaks Component-API-Eigenheit).
3. **`GET /oidc/authorize` / `POST /oidc/callback` / `GET+PUT /sso-config` / `POST /logout`
   (auth-service, öffentlich bis auf `PUT /sso-config`)** - `redirect_uri` wird gegen eine feste
   Origin-Allow-Liste geprüft (Open-Redirect-Absicherung, gleiches Prinzip wie gateway-services
   `cors_allowed_origins`). `POST /logout` ist ein komplett neuer Endpunkt - vorher gab es GAR KEINEN
   serverseitigen Logout-Mechanismus, "Abmelden" löschte nur lokale Tokens.

## Begründung

- **Warum Umfang "vollständig umsetzen, mit bekannter Testlücke" statt nur OIDC-Redirect ohne
  Kerberos**: mit dem Nutzer abgestimmt (Empfehlung angenommen) - Kerberos/SPNEGO ist der eigentliche
  "automatisch mit lokalem User eingeloggt"-Mechanismus aus der Anfrage, der reine OIDC-Redirect allein
  würde diesen Kernwunsch nicht erfüllen. Da diese Sandbox keinen echten Domain-Controller/KDC besitzt,
  ist die tatsächliche automatische Anmeldung über ein echtes Ticket hier nicht beweisbar - das
  bestehende Passwort-Formular bleibt vollständig als Fallback erhalten, kein Nutzer wird durch die
  Konfiguration ausgesperrt.
- **Warum der Code-Austausch serverseitig in `auth-service` läuft, kein PKCE**: `dms-api` ist ein
  confidential Client (hält `client_secret`, `directAccessGrantsEnabled` bereits seit jeher für das
  bestehende ROPC-Login) - der Austausch kann und soll deshalb serverseitig erfolgen, PKCE ist für
  confidential Clients mit serverseitigem Austausch nicht nötig. `state` allein genügt als
  CSRF-/Replay-Schutz (in `sessionStorage` gehalten, gegen den von Keycloak zurückgegebenen Wert
  geprüft).
- **Warum `POST /oidc/callback` die Wartungsmodus-Sperre ERST NACH dem Code-Austausch prüft** (anders als
  `/login`, das VORHER prüft): der Benutzername ist vor dem Austausch nicht bekannt (er steckt nur im
  `code`, nicht im Request-Body) - dies wurde als echte, sonst existierende Lücke selbst identifiziert
  (SSO hätte sonst die Not-Shutdown-Sperre komplett umgangen) und behoben: nach dem Austausch wird der
  Access-Token per `_validator.validate()` dekodiert und `preferred_username` gegen
  `superuser.SUPERUSER_USERNAME` geprüft; bei aktivem Wartungsmodus und Nicht-Superuser werden die frisch
  ausgestellten Tokens verworfen statt zurückgegeben.
- **Warum `POST /logout` neu eingeführt wurde**: ohne echtes serverseitiges Sitzungsende bliebe Keycloaks
  eigene SSO-Sitzung nach einem lokalen "Abmelden" bestehen - ein SPNEGO-fähiger Browser (oder einer mit
  noch gültigem Keycloak-Session-Cookie) würde sich beim nächsten Besuch sofort wieder automatisch
  anmelden. Ruft Keycloaks `.../protocol/openid-connect/logout` mit dem Refresh-Token auf (kein
  `id_token_hint` nötig, da `TokenResponse` kein ID-Token führt, kein Schema-Umbau nötig).
- **Warum zunächst nur `user-ui`**: die übrigen fünf Apps teilen zwar dieselbe `auth-context.tsx`-Struktur
  kopiert, aber ohne gemeinsames Paket - eine Ausweitung ist mit demselben Muster mechanisch, aber
  bewusst nicht Teil dieser Ad-hoc-Session (kein expliziter Nutzerwunsch für die übrigen Apps).

## Konsequenzen

- **Neue `keycloak_public_base_url`-Einstellung (`DMS_KEYCLOAK_PUBLIC_BASE_URL`)** - selbst identifizierter Bug bei der Umsetzung: `auth-service` spricht Keycloak im Compose-Stack intern über `http://keycloak:8080` an (`DMS_KEYCLOAK_BASE_URL`), dieser Hostname ist für den BROWSER, der zu `GET /oidc/authorize`s `authorization_url` navigieren soll, aber nicht auflösbar. Der bereits vor diesem Feature in `docs/services/auth-service.md` dokumentierte "Offene Punkt" ("Issuer-Hostname-Konsistenz ... sobald ein browserbasierter Redirect-Flow hinzukommt") wurde damit real - behoben über eine separate, nur von `_authorization_endpoint` genutzte öffentliche Basis-URL (`http://localhost:8080` im Compose-Stack), Token-/Logout-Endpunkte bleiben auf der internen URL.
- **`auth-service` bekommt sein zweites echtes Postgres-Schema-Objekt** (`SsoConfig`, nach
  `FederationIdentity` seit P15-S4) - gleiches `create_all`-Bootstrapping, kein Alembic in diesem Projekt.
- **`gateway-service`s `public_routes`/`maintenance_mode_allowed_routes`** um
  `auth-service:oidc/authorize`/`auth-service:oidc/callback` ergänzt (exaktes String-Match, kein
  Wildcard, existierendes Muster) - beide Endpunkte laufen vor jeder Token-Prüfung, analog zu `/login`.
- **Nicht in dieser Sandbox live verifizierbar**: das eigentliche automatische Einloggen über ein echtes
  Kerberos-Ticket (kein Domain-Controller/KDC vorhanden) - dokumentierte, mit dem Nutzer abgestimmte
  Grenze. **Vollständig verifizierbar**: die Bootstrap-Konfiguration gegen das echte laufende Keycloak,
  der komplette Redirect+Callback-Fluss über Keycloaks eigenes gehostetes Formular (der Fallback-Pfad,
  den die meisten Installationen zuerst durchlaufen), sauberes Zurückfallen bei fehlender/unvollständiger
  Kerberos-Konfiguration sowie ein echtes serverseitiges Session-Ende über `/logout` - alle Teil der neuen
  `test_bootstrap.py`-/`test_sso_flow.py`-Tests.
- **Vor der `_ensure_kerberos`-Umsetzung offen gebliebener, noch zu bestätigender Punkt**: dass
  Keycloaks eingebauter `browser`-Flow die `auth-spnego`-Ausführung tatsächlich (deaktiviert) bereits
  mitbringt, wie hier basierend auf etabliertem Keycloak-Domänenwissen angenommen - einmalig gegen das
  echte laufende Keycloak zu verifizieren, sobald der Stack wieder erreichbar ist (siehe
  `test_kerberos_enabled_creates_flow_execution_and_component`, das genau das mitprüft).
