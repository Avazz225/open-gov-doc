# 0063 — Auth-Entkopplung von Keycloak: lokale technische Konten mit eigenem Token-Issuer

**Status:** akzeptiert (Session 1 von 3, siehe Phase 18 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 18 (Nutzer-Direktive nach der "Offene Punkte"-Triage vom 2026-08-13),
betrifft `auth-service`, `libs/dms-auth-client`

## Entscheidung

Superuser-Break-Glass (4.6) und Domain-Admin-Konten hingen bislang vollständig an echten
Keycloak-Nutzerkonten — Login lief über Keycloaks Password-Grant, Break-Glass-Aktivierung über ein
Keycloak-User-Attribut (`dms_superuser_expires_at`). Ist Keycloak nicht erreichbar, ist damit auch das
Not-Shutdown-Break-Glass nicht nutzbar — ein Widerspruch zum eigentlichen Zweck eines
Notfallmechanismus. Auf ausdrücklichen Nutzerwunsch ("der Superuser soll gar nicht im Keycloak leben,
der soll nur in der App leben") werden diese Konten künftig ausschließlich in `auth-service`s eigener
Datenbank geführt, unabhängig von Keycloak.

Diese erste Session (P18-S1) legt die Infrastruktur, ohne bereits `POST /login`/Break-Glass umzustellen
(folgt in P18-S2/S3):

1. **`TechnicalAccount`** (neues Model, `auth`-Schema) — `username`, `password_hash` (bcrypt, erstes
   Mal, dass dieser Service selbst ein Passwort hasht), `account_type` (`superuser`|`domain-admin`),
   `role_name`, `enabled`, `expires_at`. Trägt dieselbe Break-Glass-Semantik wie bisher das
   Keycloak-Attribut, jetzt app-eigen.
2. **`LocalSigningKey`** (neues Model, Singleton-Zeile, gleiches Muster wie `FederationIdentity`) — ein
   eigenes RSA-2048-Schlüsselpaar, idempotent beim ersten Zugriff erzeugt (`local_token_issuer.
   ensure_signing_key`, nutzt die bereits vorhandene `federation_crypto.generate_keypair()` wieder,
   keine neue Krypto-Duplikation innerhalb desselben Service). Ein stabiler `kid` sorgt dafür, dass
   bereits ausgestellte Tokens auch über einen Neustart hinweg gültig bleiben.
3. **`GET /.well-known/jwks.json`** — liefert den öffentlichen Schlüssel im selben JWKS-Format wie
   Keycloaks `/protocol/openid-connect/certs`, ungegatet (öffentlicher Schlüssel, keine sensiblen Daten).
4. **`local_token_issuer.mint_token()`** — stellt ein Token mit identischem Claim-Shape wie Keycloak aus
   (`sub`, `preferred_username`, `realm_access.roles`, `aud`), damit Downstream-Konsumenten (`GET /me`,
   `permission-service`-Aufrufe) nichts über die Herkunft wissen müssen.
5. **`MultiIssuerTokenValidator`** (neu in `libs/dms-auth-client`) — delegiert an eine von mehreren
   `TokenValidator`-Instanzen, ausgewählt über den `iss`-Claim des jeweiligen Tokens (reines Duck-Typing,
   dieselbe `.validate(token) -> dict`-Schnittstelle wie `TokenValidator` selbst, `make_current_user_
   dependency` bemerkt den Unterschied nicht). `auth-service`s eigener `_validator` ist ab dieser Session
   ein `MultiIssuerTokenValidator` aus Keycloak- und lokalem Validator — vollständig additiv, bestehende
   Keycloak-Logins/Tokens sind unverändert gültig.

## Begründung

- **Warum ein zweiter Issuer statt eines Keycloak-Attributs mit Fallback-Login**: die zentrale
  Anforderung ist, dass Break-Glass NICHT von Keycloaks Erreichbarkeit abhängt. Ein Keycloak-Attribut
  bräuchte für die Aktivierungsprüfung selbst einen Keycloak-Zugriff — löst das Problem also nicht. Ein
  komplett getrennter, lokal signierter Token-Pfad tut das.
- **Warum derselbe Claim-Shape wie Keycloak statt eines eigenen Formats**: jeder bestehende Aufrufer
  (`GET /me`, `permission-service`s `sub`-basierte Zuordnung, Gateway-Identitäts-Header) liest bereits
  `sub`/`preferred_username`/`realm_access.roles` — ein abweichendes Format hätte Änderungen an jedem
  einzelnen Konsumenten erzwungen, für einen reinen Ausstellungswechsel unverhältnismäßig.
  `MultiIssuerTokenValidator` macht die Herkunft für alle Downstream-Konsumenten transparent.
  `libs/dms-auth-client` ist bislang nur direkt in `auth-service`/`gateway-service` instanziiert (per
  Grep bestätigt) — alle anderen Services konsumieren ausschließlich die vom Gateway weitergereichten
  `X-DMS-*`-Header, nicht selbst validierte Tokens; die Multi-Issuer-Erweiterung betrifft daher zunächst
  nur diese zwei Stellen (`gateway-service`s eigene Umstellung folgt in P18-S2, sobald `/login`
  tatsächlich lokale Tokens ausstellt).
- **Warum `bcrypt` statt eines neuen `passlib`-Unterbaus**: `bcrypt` war bereits transitiv im venv
  vorhanden (über eine andere Abhängigkeit), keine neue schwere Abhängigkeit nötig; `passlib`s
  zusätzliche Abstraktionsebene (mehrere austauschbare Hash-Schemata) hat hier keinen Konsumenten, der
  sie bräuchte — dieser Service hasht künftig ausschließlich Passwörter technischer Konten, ein einziges
  Schema genügt.
- **Warum ein `_LazyValidator`-Wrapper statt des Validators direkt**: `app.state.combined_validator`
  existiert erst nach dem Lifespan-Start (braucht einen DB-Zugriff für den persistierten
  Signierschlüssel), `get_current_user` muss aber wie bisher schon beim Modul-Import als fertige
  FastAPI-Dependency existieren. Der Wrapper verzögert nur den eigentlichen `.validate()`-Aufruf bis zum
  ersten echten Request (uvicorn nimmt ohnehin erst nach abgeschlossenem Lifespan-Start Verbindungen an)
  - reines Duck-Typing, keine Änderung an `make_current_user_dependency` selbst nötig.

## Konsequenzen

- **Zwei Token-Issuer im System, beide von `TokenValidator`/`MultiIssuerTokenValidator` akzeptiert** —
  bei künftiger Fehlersuche an `iss`-Claims denken, nicht mehr automatisch von Keycloak als einziger
  Quelle ausgehen.
- **Noch keine funktionale Änderung für Endnutzer** — `POST /login` stellt weiterhin ausschließlich
  Keycloak-Tokens aus, Superuser/Domain-Admins existieren weiterhin nur als Keycloak-Konten. Diese
  Session liefert nur die geprüfte Infrastruktur (Modell, Schlüssel, Minting, Validierung), P18-S2
  migriert den Superuser tatsächlich, P18-S3 die Domain-Admin-Konten.
- **`gateway-service`s eigener `TokenValidator` ist noch nicht auf Multi-Issuer umgestellt** — solange
  keine lokalen Tokens tatsächlich ausgestellt werden (erst ab P18-S2), ist das unkritisch; muss aber vor
  P18-S2s Abschluss nachgezogen werden, sonst würde ein frisch eingeloggter Superuser am Gateway
  scheitern, obwohl `auth-service` sein Token selbst korrekt validiert.
- Live gegen den echten laufenden Stack verifiziert: `GET /.well-known/jwks.json` liefert einen validen
  JWKS-Eintrag, ein bestehender Keycloak-Login (`users-admin`) funktioniert unverändert über `GET /me`,
  und der `kid` bleibt über einen echten Container-Neustart hinweg stabil (`local-1` vor und nach
  `docker compose restart auth-service` identisch).
