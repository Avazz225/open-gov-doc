import pytest
from license_factory import make_license_token
from license_service.license_verifier import InvalidLicenseError, decode
from license_service.settings import Settings

_PUBLIC_KEY_PEM = Settings().license_public_key_pem


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
