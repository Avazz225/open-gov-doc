# 0064 — Superuser-Migration auf lokale Tokens + Gateway-Multi-Issuer

**Status:** akzeptiert (Session 2 von 3, siehe Phase 18 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 18 Session 2, betrifft `auth-service`, `gateway-service`

## Entscheidung

Aufbauend auf der in [ADR 0063](0063-auth-entkopplung-lokale-technische-konten-dual-issuer-jwt.md) gelegten
Infrastruktur (lokaler Token-Issuer, `MultiIssuerTokenValidator`) migriert diese Session den Superuser
tatsächlich funktional von Keycloak auf `TechnicalAccount`:

1. **`superuser.py` vollständig auf async/DB umgestellt** — alle Funktionen (`ensure_superuser_account`,
   `activate`, `deactivate`, `get_status`, `get_principal_id`, `deactivate_if_expired`) nehmen jetzt einen
   `session_factory` statt `KeycloakAdmin` entgegen und operieren auf `TechnicalAccount`-Zeilen. Der
   Poll-Loop (`_superuser_poll_loop`), der NATS-Konsument (`consumer.py`) und die beiden
   `/superuser/*`-Endpunkte wurden entsprechend angepasst — strukturell unverändert (gleiches
   Poll-/Konsumenten-Idiom), nur die Datenquelle wechselt.
2. **`bootstrap.py`s Keycloak-Anteil entfernt**: `_ensure_superuser_expires_at_attribute`
   (Keycloak-Profile-Attribut-Deklaration) und der `superuser.ensure_superuser_account(admin)`-Aufruf
   fallen komplett weg — die Kontoanlage passiert jetzt in `main.py`s async Lifespan, direkt neben dem
   lokalen Signierschlüssel aus ADR 0063 (beides DB-Operationen, `bootstrap.ensure_realm_and_client` ist
   synchron und bleibt rein Keycloak-fokussiert für Domain-Admins/Realm/Client/Kerberos).
3. **`POST /login` erkennt technische Konten**: schlägt der Benutzername in `technical_account` nach,
   bevor ein Keycloak-Password-Grant versucht wird. Treffer → lokale Passwortprüfung (`bcrypt`) +
   `enabled`/`expires_at`-Prüfung, sonst unverändert der bisherige Keycloak-Pfad.
4. **`POST /refresh` erkennt lokale Tokens** über den `iss`-Claim (`local_token_issuer.is_local_token`,
   reines Peeken ohne Signaturprüfung, nur zur Weichenstellung) und stellt bei gültigem, weiterhin aktivem
   Konto ein frisches Token-Paar aus, statt Keycloaks Refresh-Grant zu versuchen (das für lokale Tokens
   ohnehin fehlschlagen würde, da keine Keycloak-Session existiert).
5. **`gateway-service`s eigener `TokenValidator` ist jetzt ein `MultiIssuerTokenValidator`** — zweite
   Instanz zeigt auf `auth-service`s `/.well-known/jwks.json` (neue `auth_service_base_url`-Einstellung,
   direkte Ost-West-Adresse wie jeder andere Cross-Service-Aufruf in diesem Projekt). Der lokale
   Issuer-String ist als Konstante in `gateway_service/main.py` dupliziert (kein gemeinsames
   `libs/`-Paket kennt ihn, `gateway-service` importiert grundsätzlich keinen Code aus `auth-service`).

## Begründung

- **Warum `POST /login`/`POST /refresh` selbst verzweigen, statt eines separaten Endpunkts**: die
  bestehenden Frontends (`user-ui` etc.) kennen nur `/login`/`/refresh` — ein separater
  `/technical-login`-Endpunkt hätte jede aufrufende Stelle ändern müssen, ohne echten Mehrwert. Die
  Verzweigung selbst ist billig (ein Tabellen-Lookup nach Benutzername vor dem Keycloak-Aufruf).
- **Warum dieselbe, generische 401-Fehlermeldung für falsches Passwort UND deaktiviertes/abgelaufenes
  Konto**: unterscheidbare Meldungen würden verraten, ob ein Konto überhaupt existiert bzw. ob es nur
  gerade deaktiviert ist — identisches Prinzip zu Keycloaks eigener, ebenso opaker Fehlermeldung für ein
  deaktiviertes Konto zuvor.
- **Warum ein `jti`-Claim ergänzt wurde (bei der Testentwicklung gefunden)**: `mint_token()` baute Claims
  nur aus `sub`/`username`/`roles`/`aud`/`iat`/`exp` — bei zwei Aufrufen für dasselbe Konto innerhalb
  derselben Sekunde (z. B. Login direkt gefolgt von Refresh) waren `iat`/`exp` identisch und alle übrigen
  Claims sowieso, wodurch zwei tatsächlich unabhängig ausgestellte Tokens byte-identisch wurden. Ein
  `jti` (registrierter JWT-Claim, `secrets.token_urlsafe(16)`) behebt das robust, unabhängig von
  Zeitauflösung.
- **Warum `role_name` auf `TechnicalAccount` nachträglich nullable gemacht wurde**: beim Entwurf in ADR
  0063 als generisch für alle künftigen technischen Konten gedacht, aber der Superuser selbst braucht
  keine permission-service-Rolle (seine Sonderrechte laufen über direkten Namensvergleich an mehreren
  Stellen im System). Da dieses Projekt ohne Alembic arbeitet (nur `create_all`), musste die bereits
  angelegte Tabellenspalte zusätzlich per manuellem `ALTER TABLE ... DROP NOT NULL` sowohl in der
  Test- als auch in der Dev-Datenbank nachgezogen werden — ein `create_all`-Aufruf allein ändert
  Spalten-Constraints einer bereits existierenden Tabelle nicht. Für künftige Modelländerungen in dieser
  frühen Entwicklungsphase relevant: Spalten-Constraint-Änderungen brauchen einen manuellen
  Nacharbeitsschritt, bis eine echte Migrationslösung eingeführt wird.

## Konsequenzen

- **Der seit P6-S6 bekannte, nie behobene Bug ("Superuser-Konto kann nicht interaktiv einloggen",
  fehlende `firstName`/`lastName`/`email` an einem historisch unvollständig angelegten Keycloak-Konto)
  ist ersatzlos verschwunden** — es gibt kein Keycloak-Konto mehr, das diesen Zustand haben könnte.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nicht nur Unit-/Integrationstests):
  Login vor Aktivierung → 401; Aktivierung; Login **über das Gateway** → 200 mit gültigem Token-Paar;
  `GET /me` über das Gateway mit dem lokalen Token → 200 (beweist `gateway-service`s eigene
  Multi-Issuer-Umstellung, nicht nur `auth-service`s); ein Aufruf gegen `document-service` mit demselben
  Token über das Gateway wurde durchgelassen (422 wegen eines fachlichen Pflichtfelds, nicht 401) —
  beweist, dass die Identität systemweit akzeptiert wird, nicht nur lokal in `auth-service`;
  `POST /refresh` über das Gateway → 200 mit frischem Paar; Deaktivierung → nachfolgender Refresh → 401.
  Ein normaler Keycloak-Login (`users-admin`) über dasselbe Gateway funktioniert unverändert.
- **`gateway-service` braucht jetzt eine neue Umgebungsvariable** (`DMS_AUTH_SERVICE_BASE_URL`) —
  in `infra/docker-compose.yml` ergänzt (`http://auth-service:8000`), fehlt sie in einer künftigen
  Deployment-Konfiguration, würden lokal ausgestellte Tokens am Gateway mit 401 scheitern, obwohl
  `auth-service` selbst sie korrekt validiert (kein stiller Fehler — die fehlende JWKS-Quelle würde beim
  ersten Validierungsversuch eines lokalen Tokens einen HTTP-Fehler beim JWKS-Abruf auslösen).
- **Domain-Admin-Konten (`users-admin`/`config-admin`) sind von dieser Session unberührt** — bleiben
  Keycloak-Konten bis P18-S3.
