# dms-auth-client

Zustandslose OIDC/JWT-Validierung gegen Keycloak (Konzept 4.4) — jeder Service prüft
Tokens selbst gegen das JWKS, ohne bei jedem Request beim Auth Service nachzufragen.

- `TokenValidator` — prüft Signatur (RS256 über JWKS), `iss`/`aud`/`exp`; wirft `InvalidTokenError`.
- `make_current_user_dependency(validator)` — baut eine FastAPI-Dependency, die den Bearer-Token prüft und bei Erfolg die Claims liefert, sonst 401.

## Nutzung

```python
from dms_auth_client import TokenValidator, make_current_user_dependency

validator = TokenValidator(
    issuer=settings.keycloak_issuer,
    audience="dms-api",
    jwks_url=f"{settings.keycloak_issuer}/protocol/openid-connect/certs",
)
get_current_user = make_current_user_dependency(validator)

@app.get("/documents")
def list_documents(user: dict = Depends(get_current_user)):
    ...
```

## Tests

Rein auf Unit-Ebene mit einem selbst signierten Testschlüssel (kein echter Keycloak
nötig) — Validierung gegen echte Keycloak-Tokens folgt automatisch mit, sobald
Auth Service (P2-S1) reale Tokens ausstellt:

```bash
uv run pytest libs/dms-auth-client/tests
```
