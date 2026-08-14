# dms-auth-client

Stateless OIDC/JWT validation against Keycloak (Concept 4.4) — each service checks
tokens itself against the JWKS, without querying the Auth Service on every request.

- `TokenValidator` — checks signature (RS256 via JWKS), `iss`/`aud`/`exp`; raises `InvalidTokenError`.
- `MultiIssuerTokenValidator` (post-roadmap feature, Phase 18, ADR 0063) — delegates to one of several `TokenValidator` instances, selected via the `iss` claim. Needed since `auth-service` can additionally issue tokens itself, alongside Keycloak, for local technical accounts (superuser/domain admins, independent of Keycloak's availability) — same `.validate(token) -> dict` interface as `TokenValidator`, `make_current_user_dependency` does not distinguish.
- `make_current_user_dependency(validator)` — builds a FastAPI dependency that checks the bearer token and returns the claims on success, otherwise 401. Accepts any object with `.validate()`, so works with both validator types.

## Usage

```python
from dms_auth_client import TokenValidator, make_current_user_dependency

validator = TokenValidator(
    issuer=settings.keycloak_issuer,
    audience="dms-api",
    jwks_url=f"{settings.keycloak_issuer}/protocol/openid-connect/certs",
)
get_current_user = make_current_user_dependency(validator)


@app.get("/documents")
def list_documents(user: dict = Depends(get_current_user)): ...
```

## Tests

Purely at the unit level with a self-signed test key (no real Keycloak
needed) — validation against real Keycloak tokens will follow automatically
once Auth Service (P2-S1) issues real tokens:

```bash
uv run pytest libs/dms-auth-client/tests
```
