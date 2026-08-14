import hashlib
from datetime import datetime

import aioboto3
from botocore.exceptions import ClientError

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class S3Backend(StorageBackend):
    """S3-compatible backend (3.6) - defaults to MinIO, works identically
    against any S3-compatible provider (AWS S3, Ceph RGW, ...)."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        object_lock_enabled: bool = False,
    ) -> None:
        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region
        # WORM/Object Lock (5.1/5.2a, since P7-S1) - see
        # `BackendTargetConfig.object_lock_mode`. Only relevant for
        # `ensure_bucket()`/`write()`; `delete()` receives
        # `bypass_governance` directly from the caller regardless.
        self._object_lock_enabled = object_lock_enabled

    def _client(self):
        return self._session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        )

    async def ensure_bucket(self) -> None:
        async with self._client() as s3:
            try:
                await s3.head_bucket(Bucket=self._bucket)
            except ClientError:
                # `ObjectLockEnabledForBucket` CANNOT be enabled after the
                # fact on an already-existing bucket (S3 API limitation) -
                # this branch therefore deliberately only applies on the
                # very first creation. A bucket that has long existed (as
                # in this development environment) remains untouched via
                # the `head_bucket` success branch above, even if
                # `object_lock_mode` is set afterward - see ADR 0030 for
                # why automatic migration was deliberately not implemented.
                kwargs = {"ObjectLockEnabledForBucket": True} if self._object_lock_enabled else {}
                await s3.create_bucket(Bucket=self._bucket, **kwargs)

    async def write(self, key: str, data: bytes, *, lock_until: datetime | None = None) -> None:
        async with self._client() as s3:
            kwargs = {}
            if lock_until is not None:
                kwargs["ObjectLockMode"] = "GOVERNANCE"
                kwargs["ObjectLockRetainUntilDate"] = lock_until
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, **kwargs)

    async def read(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                raise ObjectNotFoundError(key) from exc
            return await response["Body"].read()

    async def delete(self, key: str, *, bypass_governance: bool = False) -> None:
        async with self._client() as s3:
            kwargs: dict = {}
            if self._object_lock_enabled:
                # Object Lock buckets always have versioning enabled (see
                # ensure_bucket). A `delete_object()` WITHOUT VersionId on a
                # versioned bucket would only create a delete marker and
                # leave the actual, locked version untouched (and still
                # billable) - Governance mode can only take effect when the
                # specific version is addressed explicitly.
                try:
                    head = await s3.head_object(Bucket=self._bucket, Key=key)
                except ClientError:
                    return  # already gone - idempotent as usual
                version_id = head.get("VersionId")
                if version_id:
                    kwargs["VersionId"] = version_id
            if bypass_governance:
                kwargs["BypassGovernanceRetention"] = True
            await s3.delete_object(Bucket=self._bucket, Key=key, **kwargs)

    async def exists(self, key: str) -> bool:
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except ClientError:
                return False

    async def checksum(self, key: str) -> str:
        data = await self.read(key)
        return hashlib.sha256(data).hexdigest()
