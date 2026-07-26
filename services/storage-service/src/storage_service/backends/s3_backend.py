import hashlib

import aioboto3
from botocore.exceptions import ClientError

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class S3Backend(StorageBackend):
    """S3-kompatibles Backend (3.6) - Werkseinstellung MinIO, funktioniert
    identisch gegen jeden S3-kompatiblen Provider (AWS S3, Ceph RGW, ...)."""

    def __init__(
        self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str, region: str
    ) -> None:
        self._session = aioboto3.Session()
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket
        self._region = region

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
                await s3.create_bucket(Bucket=self._bucket)

    async def write(self, key: str, data: bytes) -> None:
        async with self._client() as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data)

    async def read(self, key: str) -> bytes:
        async with self._client() as s3:
            try:
                response = await s3.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                raise ObjectNotFoundError(key) from exc
            return await response["Body"].read()

    async def delete(self, key: str) -> None:
        async with self._client() as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

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
