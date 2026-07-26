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


def test_valid_bearer_token_allows_access(jwks, make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    client = TestClient(build_app(validator))
    token = make_token()

    response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"sub": "user-123"}


def test_missing_token_is_rejected(jwks, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    client = TestClient(build_app(validator))

    response = client.get("/me")

    assert response.status_code == 401


def test_invalid_token_returns_401(jwks, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    client = TestClient(build_app(validator))

    response = client.get("/me", headers={"Authorization": "Bearer not-a-real-token"})

    assert response.status_code == 401
