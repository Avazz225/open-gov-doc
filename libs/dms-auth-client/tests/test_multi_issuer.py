import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from dms_auth_client import InvalidTokenError, MultiIssuerTokenValidator, TokenValidator
from jose import jwk, jwt

SECOND_ISSUER = "https://auth-service.local/technical"
SECOND_KID = "local-technical-key-1"


@pytest.fixture(scope="session")
def second_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@pytest.fixture(scope="session")
def second_jwks(second_keypair):
    _, public_pem = second_keypair
    public_jwk = jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = SECOND_KID
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return {"keys": [public_jwk]}


@pytest.fixture
def make_second_token(second_keypair):
    def _make_token(*, expires_in=300, extra_claims=None):
        private_pem, _ = second_keypair
        claims = {
            "iss": SECOND_ISSUER,
            "aud": "dms-api",
            "sub": "technical-account-1",
            "exp": int(time.time()) + expires_in,
            **(extra_claims or {}),
        }
        return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": SECOND_KID})

    return _make_token


def _multi_validator(jwks, second_jwks, issuer, audience):
    return MultiIssuerTokenValidator(
        [
            TokenValidator(issuer=issuer, audience=audience, jwks=jwks),
            TokenValidator(issuer=SECOND_ISSUER, audience=audience, jwks=second_jwks),
        ]
    )


def test_routes_keycloak_token_to_matching_validator(
    jwks, second_jwks, make_token, issuer, audience
):
    validator = _multi_validator(jwks, second_jwks, issuer, audience)
    token = make_token()

    claims = validator.validate(token)

    assert claims["sub"] == "user-123"
    assert claims["iss"] == issuer


def test_routes_local_technical_token_to_matching_validator(
    jwks, second_jwks, make_second_token, issuer, audience
):
    validator = _multi_validator(jwks, second_jwks, issuer, audience)
    token = make_second_token()

    claims = validator.validate(token)

    assert claims["sub"] == "technical-account-1"
    assert claims["iss"] == SECOND_ISSUER


def test_unknown_issuer_is_rejected(jwks, second_jwks, make_token, issuer, audience):
    validator = _multi_validator(jwks, second_jwks, issuer, audience)
    token = make_token(issuer="https://someone-else.example/realms/dms")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_requires_at_least_one_validator():
    with pytest.raises(ValueError):
        MultiIssuerTokenValidator([])
