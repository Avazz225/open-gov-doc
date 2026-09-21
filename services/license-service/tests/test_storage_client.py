import os

from license_service.clients import StorageClient

STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")


async def test_storage_client_sends_a_trusted_principal_header():
    """P67-S2: `StorageClient.total_bytes()` never sent `X-DMS-Principal` at
    all, a real, currently-broken production bug found via `dms license
    status`'s live verification (a real `500`, traced to storage-service's
    `403`) - the same class of bug P66-S1 already fixed once for
    `reporting-service`'s `StorageClient`, missed for this equally real
    caller of the exact same `GET /storage/usage` endpoint. Real HTTP
    against the live-running `storage-service` container, not mocked - a
    mock would not have caught this class of bug (every mocked call in
    `test_usage.py`/`test_poll_loop.py`/`test_api.py` "succeeds" regardless
    of headers)."""
    client = StorageClient(STORAGE_SERVICE_URL)
    try:
        total = await client.total_bytes()
        assert isinstance(total, int)
    finally:
        await client.close()
