# dms-auth-client

Zustandslose OIDC/JWT-Validierung gegen Keycloak (Konzept 4.4) — jeder Service prüft
Tokens selbst gegen das JWKS, ohne bei jedem Request beim Auth Service nachzufragen.

- `TokenValidator` — prüft Signatur (RS256 über JWKS), `iss`/`aud`/`exp`; wirft `InvalidTokenError`.
- `MultiIssuerTokenValidator` (Post-Roadmap-Feature, Phase 18, ADR 0063) — delegiert an eine von mehreren `TokenValidator`-Instanzen, ausgewählt über den `iss`-Claim. Nötig, seit `auth-service` zusätzlich zu Keycloak selbst Tokens für lokale technische Konten (Superuser/Domain-Admins, unabhängig von Keycloaks Erreichbarkeit) ausstellen kann — dieselbe `.validate(token) -> dict`-Schnittstelle wie `TokenValidator`, `make_current_user_dependency` unterscheidet nicht.
- `make_current_user_dependency(validator)` — baut eine FastAPI-Dependency, die den Bearer-Token prüft und bei Erfolg die Claims liefert, sonst 401. Nimmt jedes Objekt mit `.validate()` entgegen, funktioniert also mit beiden Validator-Typen.

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
def list_documents(user: dict = Depends(get_current_user)): ...
```

## Tests

Rein auf Unit-Ebene mit einem selbst signierten Testschlüssel (kein echter Keycloak
nötig) — Validierung gegen echte Keycloak-Tokens folgt automatisch mit, sobald
Auth Service (P2-S1) reale Tokens ausstellt:

```bash
uv run pytest libs/dms-auth-client/tests
```
