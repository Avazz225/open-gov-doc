import os

import pytest
from archival_service import crypto


def test_encrypt_decrypt_roundtrip():
    key = os.urandom(32)
    data = b"archiv-inhalt, geheim"

    ciphertext = crypto.encrypt(data, key)

    assert ciphertext != data
    assert crypto.decrypt(ciphertext, key) == data


def test_decrypt_with_wrong_key_raises():
    key = os.urandom(32)
    wrong_key = os.urandom(32)
    ciphertext = crypto.encrypt(b"geheim", key)

    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(ciphertext, wrong_key)


def test_encrypt_uses_fresh_nonce_each_time():
    key = os.urandom(32)
    data = b"gleicher inhalt"

    first = crypto.encrypt(data, key)
    second = crypto.encrypt(data, key)

    assert first != second
