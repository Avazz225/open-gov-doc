import os
import uuid

from reporting_service.clients import StorageClient

STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")


async def test_storage_client_sends_a_trusted_principal_header():
    """P66-S1: `StorageClient` never sent `X-DMS-Principal` at all, a real,
    currently-broken bug - `storage-service`'s object-CRUD endpoints have
    required a trusted caller identity since ADR 0179 (P59-S2), and
    `reporting-service` was simply never added to that allowlist or given
    a header to send. Real HTTP against the live-running `storage-service`
    container, not mocked - a mock would not have caught this class of bug
    (every mocked call in `test_api.py` "succeeds" regardless of headers)."""
    client = StorageClient(STORAGE_SERVICE_URL)
    try:
        key = f"reports/p66s1-test/{uuid.uuid4().hex}.txt"
        await client.upload(key, b"trusted-caller-header-smoke-test", "text/plain")
        downloaded = await client.download(key)
        assert downloaded == b"trusted-caller-header-smoke-test"

        usage = await client.get_usage()
        assert isinstance(usage, list)
    finally:
        await client.close()
