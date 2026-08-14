# auth-service

Thin OIDC broker in front of Keycloak (concept 4.4) — holds the client secret,
callers only see username/password or finished tokens.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/login` | Password grant against Keycloak, returns tokens |
| `POST` | `/refresh` | Exchange refresh token for new tokens |
| `GET` | `/me` | Validates bearer token, returns normalized identity |
| `GET` | `/users` | List users (since P4-S3, admin UI) |
| `POST` | `/users` | Create user |
| `DELETE` | `/users/{id}` | Delete user |
| `GET` | `/healthz` | Own health check |

## Realm/client bootstrap

On startup, realm `dms` and client `dms-api` are created idempotently (analogous
to the `CREATE SCHEMA IF NOT EXISTS` pattern of the DB services), including an
audience mapper — without it, the access token only carries `aud: "account"`
(Keycloak default), not the actual client name.

**Pitfall when creating test users**: Keycloak 25 requires `firstName`/`lastName`
per the default user profile; if missing, login fails with "Account is not fully
set up" instead of showing a clear error.

## Local/AD accounts (concept 4.4)

Both account types are already covered by Keycloak itself (a realm can
manage local and LDAP/AD-federated users simultaneously) — no separate
user table is needed in this service. AD group→internal-role mapping is not
yet implemented (see `docs/services/auth-service.md`, "Open items").

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres keycloak auth-service
curl localhost:8003/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres keycloak && cd ..
uv run pytest services/auth-service/tests
```
