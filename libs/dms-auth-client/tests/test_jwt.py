import pytest
from dms_auth_client import InvalidTokenError, TokenValidator


def test_valid_token_returns_claims(jwks, make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    token = make_token()

    claims = validator.validate(token)

    assert claims["sub"] == "user-123"
    assert claims["iss"] == issuer


def test_wrong_audience_is_rejected(jwks, make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    token = make_token(audience="someone-else")

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_expired_token_is_rejected(jwks, make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    token = make_token(expires_in=-10)

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_unknown_kid_is_rejected(make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks={"keys": []})
    token = make_token()

    with pytest.raises(InvalidTokenError):
        validator.validate(token)


def test_tampered_signature_is_rejected(jwks, make_token, issuer, audience):
    validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
    token = make_token()
    tampered = token[:-4] + ("A" * 4)

    with pytest.raises(InvalidTokenError):
        validator.validate(tampered)
