import time
from unittest.mock import patch

import pytest
from query_service.dry_run_tokens import InvalidDryRunTokenError, decode, issue_token

SECRET = "test-secret"


def test_issue_and_decode_roundtrip():
    token = issue_token(
        action_type="document.attribute_reset",
        params={"document_id": "doc-1", "attribute_key": "notiz"},
        principal_id="alice",
        secret=SECRET,
        ttl_seconds=300,
    )
    claims = decode(token, secret=SECRET)
    assert claims["action_type"] == "document.attribute_reset"
    assert claims["params"] == {"document_id": "doc-1", "attribute_key": "notiz"}
    assert claims["principal_id"] == "alice"


def test_decode_rejects_wrong_secret():
    token = issue_token(
        action_type="document.attribute_reset",
        params={},
        principal_id="alice",
        secret=SECRET,
        ttl_seconds=300,
    )
    with pytest.raises(InvalidDryRunTokenError):
        decode(token, secret="wrong-secret")


def test_decode_rejects_tampered_payload():
    token = issue_token(
        action_type="document.attribute_reset",
        params={"document_id": "doc-1"},
        principal_id="alice",
        secret=SECRET,
        ttl_seconds=300,
    )
    payload, signature = token.split(".", 1)
    tampered = f"{payload}x.{signature}"
    with pytest.raises(InvalidDryRunTokenError):
        decode(tampered, secret=SECRET)


def test_decode_rejects_malformed_token():
    with pytest.raises(InvalidDryRunTokenError):
        decode("not-a-valid-token", secret=SECRET)


def test_decode_rejects_expired_token():
    token = issue_token(
        action_type="document.attribute_reset",
        params={},
        principal_id="alice",
        secret=SECRET,
        ttl_seconds=1,
    )
    with patch("query_service.dry_run_tokens.time.time", return_value=time.time() + 10):
        with pytest.raises(InvalidDryRunTokenError):
            decode(token, secret=SECRET)
