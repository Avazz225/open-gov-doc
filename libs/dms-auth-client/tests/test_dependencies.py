from conftest import AUDIENCE, ISSUER, make_token
from dms_auth_client import TokenValidator, make_current_user_dependency
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def build_app(validator: TokenValidator) -> FastAPI:
    app = FastAPI()
    current_user = make_current_user_dependency(validator)

    @app.get("/me")
    def me(user: dict = Depends(current_user)):
        return {"sub": user["sub"]}

    return app


def test_valid_bearer_token_allows_access(rsa_keypair, jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    client = TestClient(build_app(validator))
    token = make_token(rsa_keypair)

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"sub": "user-123"}


def test_missing_token_is_rejected(jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    client = TestClient(build_app(validator))

    response = client.get("/me")

    assert response.status_code == 401


def test_invalid_token_returns_401(jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    client = TestClient(build_app(validator))

    response = client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401
