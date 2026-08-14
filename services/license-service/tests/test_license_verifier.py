import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from license_factory import make_license_token
from license_service.license_verifier import InvalidLicenseError, decode
from license_service.settings import Settings

_PUBLIC_KEY_PEM = Settings().license_public_key_pem


def _generate_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_pem, public_pem


def test_decode_valid_token_returns_claims():
    token = make_license_token(max_users=5)

    claims = decode(token, public_key_pem=_PUBLIC_KEY_PEM)

    assert claims["max_users"] == 5
    assert claims["user_model"] == "concurrent"


def test_decode_expired_token_still_returns_claims():
    """Verifikation prueft nur die Signatur, nicht die Gueltigkeit (siehe
    usage.py) - eine abgelaufene, aber signaturgueltige Lizenz ist ein
    Statuszustand, keine Fehlerbedingung beim Dekodieren selbst."""
    token = make_license_token(expires_in_days=-10)

    claims = decode(token, public_key_pem=_PUBLIC_KEY_PEM)

    assert claims["exp"] < claims["iat"]


def test_decode_with_wrong_key_raises():
    token = make_license_token()
    # Ein echter, aber ANDERER RSA-Public-Key - erzwingt einen echten
    # Signaturpruefungs-Fehlschlag statt eines PEM-Parsing-Fehlers.
    other_public_key_pem = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAs1f1UtkW9RVLizapkHRN
TFt/Z4o6amZggdywoed5mdt7CZU7q3oyD3RWpCh/mTazvf5+XahrAbYsb0c4jJWj
MJzhQAL+v1g37MmW547+vEik6i+rdsfKt+O9dmXPHIeconSutIkDRYSfSPRkpHtN
nJCtqICwxhL3q4fLfdCAQifAutfe36QaknuNjwyQ4XEYXAfFaMmvM8wWDW4YSDii
CHukyQ9nqttfHW79KSM/c11jOdLJJX4u20rwX4LVuSKVNKeRlLEwlMfB4EBuwqiQ
D8PZzssZIMlSEyXsv4xbhZ9PSWU7zKE2wSvMfRzJO92xJy2+YstQn2a4cyDj5q39
EQIDAQAB
-----END PUBLIC KEY-----"""

    with pytest.raises(InvalidLicenseError):
        decode(token, public_key_pem=other_public_key_pem)


def test_decode_garbage_token_raises():
    with pytest.raises(InvalidLicenseError):
        decode("not-a-jwt", public_key_pem=_PUBLIC_KEY_PEM)


def test_decode_falls_back_to_previous_key_during_rotation_window():
    """Post-Roadmap Phase 21 Session 1 (ADR 0084): waehrend einer laufenden
    Lizenzgeber-Schluesselrotation bleibt eine bereits installierte, unter
    dem ALTEN Schluessel signierte Lizenz gueltig, solange der Betreiber
    ``previous_public_key_pem`` auf den alten Schluessel gesetzt hat."""
    old_private_pem, old_public_pem = _generate_keypair()
    claims = {"iat": 0, "exp": 9999999999, "user_model": "concurrent", "max_users": 5}
    token_signed_with_old_key = jwt.encode(claims, old_private_pem, algorithm="RS256")

    decoded = decode(
        token_signed_with_old_key,
        public_key_pem=_PUBLIC_KEY_PEM,
        previous_public_key_pem=old_public_pem,
    )

    assert decoded["max_users"] == 5


def test_decode_prefers_current_key_without_needing_fallback():
    token_signed_with_current_key = make_license_token(max_users=7)
    _, unrelated_public_pem = _generate_keypair()

    decoded = decode(
        token_signed_with_current_key,
        public_key_pem=_PUBLIC_KEY_PEM,
        previous_public_key_pem=unrelated_public_pem,
    )

    assert decoded["max_users"] == 7


def test_decode_raises_when_neither_current_nor_previous_key_matches():
    _, unrelated_public_pem_a = _generate_keypair()
    _, unrelated_public_pem_b = _generate_keypair()
    token = make_license_token()

    with pytest.raises(InvalidLicenseError):
        decode(
            token,
            public_key_pem=unrelated_public_pem_a,
            previous_public_key_pem=unrelated_public_pem_b,
        )


def test_decode_without_previous_key_configured_behaves_as_before():
    token = make_license_token(max_users=3)

    decoded = decode(token, public_key_pem=_PUBLIC_KEY_PEM, previous_public_key_pem=None)

    assert decoded["max_users"] == 3
