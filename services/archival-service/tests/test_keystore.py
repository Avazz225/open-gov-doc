import base64

import pytest
from archival_service.keystore import EnvKeyStore, KeyNotFoundError


def test_env_keystore_returns_configured_key():
    key = base64.b64encode(b"0" * 32).decode("ascii")
    store = EnvKeyStore(key)

    assert store.get_key("default") == b"0" * 32


def test_env_keystore_raises_without_configured_key():
    store = EnvKeyStore(None)

    with pytest.raises(KeyNotFoundError):
        store.get_key("default")
