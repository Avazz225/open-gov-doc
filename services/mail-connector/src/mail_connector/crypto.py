import base64
import binascii
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_LENGTH = 12


class CredentialKeyError(Exception):
    """No usable `DMS_MAILBOX_CREDENTIALS_KEY`: mailbox passwords can't be
    stored or read (P88-S4a, ADR 0244)."""


class DecryptionError(Exception):
    pass


def load_key(encoded: str | None) -> bytes:
    """The base64-encoded 32-byte AES-256 key from the environment. Same
    shape as the pseudonymization-vault keys (ADR 0156); deliberately
    duplicated per service, like `document_service.crypto`."""
    if not encoded:
        raise CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY ist nicht gesetzt")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY ist kein gültiges Base64") from exc
    if len(key) != 32:
        raise CredentialKeyError("DMS_MAILBOX_CREDENTIALS_KEY muss 32 Byte lang sein (AES-256)")
    return key


def encrypt(plaintext: str, key: bytes) -> bytes:
    """AES-256-GCM with a random nonce prepended - a mailbox password at
    rest (P88-S4a). The plaintext only ever exists in this process's
    memory while a backend connects."""
    nonce = os.urandom(_NONCE_LENGTH)
    return nonce + AESGCM(key).encrypt(nonce, plaintext.encode(), None)


def decrypt(data: bytes, key: bytes) -> str:
    nonce, ciphertext = data[:_NONCE_LENGTH], data[_NONCE_LENGTH:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except Exception as exc:
        raise DecryptionError(f"Postfach-Passwort nicht entschlüsselbar: {exc}") from exc
