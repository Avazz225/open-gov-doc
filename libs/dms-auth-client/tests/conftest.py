import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

ISSUER = "https://keycloak.test/realms/dms"
AUDIENCE = "dms-api"
KID = "test-key-1"


@pytest.fixture(scope="session")
def issuer() -> str:
    return ISSUER


@pytest.fixture(scope="session")
def audience() -> str:
    return AUDIENCE


@pytest.fixture(scope="session")
def rsa_keypair():
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
def jwks(rsa_keypair):
    _, public_pem = rsa_keypair
    public_jwk = jwk.construct(public_pem, algorithm="RS256").to_dict()
    public_jwk["kid"] = KID
    public_jwk["alg"] = "RS256"
    public_jwk["use"] = "sig"
    return {"keys": [public_jwk]}


@pytest.fixture
def make_token(rsa_keypair):
    """Fabrik statt Modul-Import, damit Testdateien mit `--import-mode=importlib`
    (nötig, weil mehrere Services gleichnamige Testmodule wie test_repository.py
    haben) das nicht per `from conftest import ...` einbinden müssen.
    """

    def _make_token(*, issuer=ISSUER, audience=AUDIENCE, expires_in=300, extra_claims=None):
        private_pem, _ = rsa_keypair
        claims = {
            "iss": issuer,
            "aud": audience,
            "sub": "user-123",
            "exp": int(time.time()) + expires_in,
            **(extra_claims or {}),
        }
        return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": KID})

    return _make_token
