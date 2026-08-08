from datetime import datetime

from pydantic import BaseModel


class LicenseUploadRequest(BaseModel):
    license_token: str


class DimensionUsageOut(BaseModel):
    limit: float | int | None
    current: float | int | None
    exceeded: bool


class LicenseStatusOut(BaseModel):
    installed: bool
    valid: bool
    invalid_reason: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    days_remaining: int | None = None
    user_model: str | None = None
    users: DimensionUsageOut | None = None
    storage_gb: DimensionUsageOut | None = None
    documents: DimensionUsageOut | None = None
    licensed_components: list[str] | None = None
    limits_exceeded: list[str] = []
