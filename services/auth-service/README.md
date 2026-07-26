# auth-service

Schlanker OIDC-Broker vor Keycloak (Konzept 4.4) — hält den Client-Secret,
Aufrufer sehen nur Benutzername/Passwort bzw. fertige Tokens.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/login` | Password-Grant gegen Keycloak, liefert Tokens |
| `POST` | `/refresh` | Refresh-Token gegen neue Tokens tauschen |
| `GET` | `/me` | Validiert Bearer-Token, liefert normalisierte Identität |
| `GET` | `/users` | Nutzer auflisten (seit P4-S3, Admin-UI) |
| `POST` | `/users` | Nutzer anlegen |
| `DELETE` | `/users/{id}` | Nutzer löschen |
| `GET` | `/healthz` | Eigener Health-Check |

## Realm-/Client-Bootstrap

Beim Start wird Realm `dms` und Client `dms-api` idempotent angelegt (analog
zum `CREATE SCHEMA IF NOT EXISTS`-Muster der DB-Services), inkl. eines
Audience-Mappers — ohne den trägt der Access-Token nur `aud: "account"`
(Keycloak-Default), nicht den eigenen Client-Namen.

**Falle bei der Testnutzer-Anlage**: Keycloak 25 verlangt per Default-User-Profile
`firstName`/`lastName`; fehlen sie, schlägt der Login mit "Account is not fully
set up" fehl, statt einen klaren Fehler zu zeigen.

## Lokale/AD-Konten (Konzept 4.4)

Beide Kontotypen sind bereits durch Keycloak selbst abgedeckt (ein Realm kann
lokale und LDAP/AD-föderierte Nutzer gleichzeitig verwalten) — keine eigene
Nutzertabelle in diesem Service nötig. AD-Gruppe→interne-Rolle-Mapping ist noch
nicht implementiert (siehe `docs/services/auth-service.md`, "Offene Punkte").

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an (Heartbeat, Deregister beim Shutdown) - Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, siehe `docs/services/gateway-service.md` für den Konsumenten (API-Gateway, dynamisches Routing).

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres keycloak auth-service
curl localhost:8003/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres keycloak && cd ..
uv run pytest services/auth-service/tests
```
