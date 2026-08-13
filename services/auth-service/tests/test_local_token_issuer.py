from auth_service import local_token_issuer
from auth_service.main import app
from dms_auth_client import TokenValidator
from fastapi.testclient import TestClient


def test_hash_and_verify_password_roundtrip():
    hashed = local_token_issuer.hash_password("correct horse battery staple")

    assert local_token_issuer.verify_password("correct horse battery staple", hashed) is True
    assert local_token_issuer.verify_password("wrong password", hashed) is False


def test_ensure_signing_key_is_idempotent():
    """Simuliert einen echten Neustart (zwei unabhängige, SEQUENZIELLE
    `TestClient`-Kontexte durchlaufen je den vollen Lifespan, eigener
    Event-Loop je Kontext - `ensure_signing_key` lässt sich nicht einfach
    manuell außerhalb des von `TestClient` verwalteten Loops aufrufen, da
    asyncpg-Verbindungen an genau einen Loop gebunden sind). Bewusst NICHT
    die `client`-Fixture verwendet: ein zweiter, gleichzeitig offener
    Lifespan-Kontext würde am selben NATS-Durable-Konsumentennamen
    scheitern ("already bound to a subscription") - der erste Kontext muss
    also erst vollständig schließen, bevor der zweite öffnet."""
    with TestClient(app):
        first_kid = app.state.local_signing_key.kid
        first_private_pem = app.state.local_signing_key.private_key_pem

    with TestClient(app):
        second_kid = app.state.local_signing_key.kid
        second_private_pem = app.state.local_signing_key.private_key_pem

    assert first_kid == second_kid
    assert first_private_pem == second_private_pem


def test_mint_token_validates_against_its_own_jwks(client):
    signing_key = app.state.local_signing_key
    jwks = local_token_issuer.build_jwks(signing_key.public_key_pem, signing_key.kid)
    token = local_token_issuer.mint_token(
        private_key_pem=signing_key.private_key_pem,
        kid=signing_key.kid,
        audience="dms-api",
        subject="technical-1",
        username="superuser",
        roles=["dms-admin"],
        expires_in_seconds=300,
    )

    validator = TokenValidator(
        issuer=local_token_issuer.LOCAL_ISSUER, audience="dms-api", jwks=jwks
    )
    claims = validator.validate(token)

    assert claims["sub"] == "technical-1"
    assert claims["preferred_username"] == "superuser"
    assert claims["realm_access"]["roles"] == ["dms-admin"]


def test_jwks_endpoint_matches_persisted_key(client):
    signing_key = app.state.local_signing_key

    response = client.get("/.well-known/jwks.json")

    assert response.status_code == 200
    body = response.json()
    assert body["keys"][0]["kid"] == signing_key.kid


def test_locally_minted_token_is_accepted_by_me_endpoint(client):
    """Beweist die `MultiIssuerTokenValidator`-Verdrahtung end-to-end: ein
    lokal (nicht von Keycloak) ausgestelltes Token wird von `GET /me` -
    demselben Endpunkt, der auch echte Keycloak-Tokens akzeptiert -
    genauso angenommen, ohne dass `/login` selbst schon lokale Konten
    ausstellen müsste (folgt erst in Phase 18 Session 2)."""
    signing_key = app.state.local_signing_key
    token = local_token_issuer.mint_token(
        private_key_pem=signing_key.private_key_pem,
        kid=signing_key.kid,
        audience="dms-api",
        subject="technical-2",
        username="config-admin",
        roles=["dms-admin"],
        expires_in_seconds=300,
    )

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["sub"] == "technical-2"
    assert body["username"] == "config-admin"
