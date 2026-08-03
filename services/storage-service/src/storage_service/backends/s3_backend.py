import hashlib
from datetime import datetime

import aioboto3
from botocore.exceptions import ClientError

from storage_service.backends.interface import ObjectNotFoundError, StorageBackend


class S3Backend(StorageBackend):
    """S3-kompatibles Backend (3.6) - Werkseinstellung MinIO, funktioniert
    identisch gegen jeden S3-kompatiblen Provider (AWS S3, Ceph RGW, ...)."""

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
        # WORM/Object-Lock (5.1/5.2a, seit P7-S1) - siehe
        # `BackendTargetConfig.object_lock_mode`. Nur relevant für
        # `ensure_bucket()`/`write()`, `delete()` erhält `bypass_governance`
        # unabhängig davon direkt vom Aufrufer.
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
                # `ObjectLockEnabledForBucket` lässt sich NICHT nachträglich
                # auf einen bereits bestehenden Bucket aktivieren (S3-API-
                # Grenze) - dieser Zweig greift deshalb bewusst nur bei der
                # allerersten Anlage. Ein längst existierender Bucket (wie in
                # dieser Entwicklungsumgebung) bleibt über den
                # `head_bucket`-Erfolgszweig oben unangetastet, auch wenn
                # `object_lock_mode` nachträglich gesetzt wird - siehe ADR
                # 0030 für die bewusst unterlassene automatische Migration.
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
                # Object-Lock-Buckets haben zwingend Versionierung aktiv
                # (siehe ensure_bucket). Ein `delete_object()` OHNE VersionId
                # würde auf einem versionierten Bucket nur einen Delete-
                # Marker anlegen und die eigentliche, gesperrte Version
                # unangetastet (und weiter abrechenbar) zurücklassen -
                # Governance-Mode kann nur greifen, wenn die konkrete Version
                # explizit adressiert wird.
                try:
                    head = await s3.head_object(Bucket=self._bucket, Key=key)
                except ClientError:
                    return  # bereits nicht (mehr) vorhanden - idempotent wie sonst auch
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
