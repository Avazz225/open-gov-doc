import pytest
from conftest import AUDIENCE, ISSUER, make_token
from dms_auth_client import InvalidTokenError, TokenValidator


def test_valid_token_returns_claims(rsa_keypair, jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    token = make_token(rsa_keypair)

    claims = validator.validate(token)

    assert claims["sub"] == "user-123"
    assert claims["iss"] == ISSUER


def test_wrong_audience_is_rejected(rsa_keypair, jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    token = make_token(rsa_keypair, audience="someone-else")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_expired_token_is_rejected(rsa_keypair, jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    token = make_token(rsa_keypair, expires_in=-10)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_unknown_kid_is_rejected(rsa_keypair):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks={"keys": []})
    token = make_token(rsa_keypair)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_tampered_signature_is_rejected(rsa_keypair, jwks):
    validator = TokenValidator(issuer=ISSUER, audience=AUDIENCE, jwks=jwks)
    token = make_token(rsa_keypair)
    tampered = token[:-4] + ("A" * 4)

    with pytest.raises(InvalidTokenError):
        validator.validate(tampered)
