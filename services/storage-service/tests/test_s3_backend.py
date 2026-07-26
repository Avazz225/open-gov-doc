import hashlib
import os
import uuid

import pytest
from storage_service.backends.interface import ObjectNotFoundError
from storage_service.backends.s3_backend import S3Backend

ENDPOINT_URL = os.environ.get("TEST_S3_ENDPOINT_URL", "http://localhost:9000")
ACCESS_KEY = os.environ.get("TEST_S3_ACCESS_KEY", "dms_minio")
SECRET_KEY = os.environ.get("TEST_S3_SECRET_KEY", "dms_minio_dev_only")


@pytest.fixture
async def backend():
    bucket = f"test-{uuid.uuid4().hex[:8]}"
    b = S3Backend(
        endpoint_url=ENDPOINT_URL,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        bucket=bucket,
        region="us-east-1",
    )
    await b.ensure_bucket()
    yield b
    async with b._client() as s3:  # noqa: SLF001 - Testaufräumen, kein Produktionscode
        response = await s3.list_objects_v2(Bucket=bucket)
        for obj in response.get("Contents", []):
            await s3.delete_object(Bucket=bucket, Key=obj["Key"])
        await s3.delete_bucket(Bucket=bucket)


async def test_ensure_bucket_is_idempotent(backend):
    await backend.ensure_bucket()  # zweiter Aufruf darf nicht scheitern


async def test_write_and_read_roundtrip(backend):
    await backend.write("foo/bar.txt", b"hello")

    assert await backend.read("foo/bar.txt") == b"hello"


async def test_exists(backend):
    assert await backend.exists("missing.txt") is False

    await backend.write("present.txt", b"x")

    assert await backend.exists("present.txt") is True


async def test_read_missing_raises(backend):
    with pytest.raises(ObjectNotFoundError):
        await backend.read("missing.txt")


async def test_delete_removes_object(backend):
    await backend.write("todelete.txt", b"x")

    await backend.delete("todelete.txt")

    assert await backend.exists("todelete.txt") is False


async def test_checksum_matches_sha256(backend):
    await backend.write("f.txt", b"hello world")

    checksum = await backend.checksum("f.txt")

    assert checksum == hashlib.sha256(b"hello world").hexdigest()


async def test_overwrite_replaces_content(backend):
    await backend.write("f.txt", b"first")
    await backend.write("f.txt", b"second")

    assert await backend.read("f.txt") == b"second"
