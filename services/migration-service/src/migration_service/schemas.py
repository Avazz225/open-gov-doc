from datetime import datetime

from pydantic import BaseModel


class PairedInstallationCreate(BaseModel):
    display_name: str
    base_url: str
    # Leave empty if this installation is initiating the PAIRING (a new
    # key is generated and returned once) - set if the key already issued
    # by the other side is entered here (see
    # docs/services/migration-service.md "Pairing").
    api_key: str | None = None


class PairedInstallationOut(BaseModel):
    id: str
    display_name: str
    base_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PairedInstallationCreateOut(PairedInstallationOut):
    # Only included in the creation response (never in GET/List) - identical
    # "return once, never send again" principle as federation-hub-service,
    # even though it is additionally stored internally here.
    api_key: str


class TransferCreate(BaseModel):
    source_folder_id: str
    target_installation_id: str
    created_by: str
    dry_run: bool = False
    retention_days: int | None = None


class TransferOut(BaseModel):
    id: str
    source_folder_id: str
    target_installation_id: str
    dry_run: bool
    retention_days: int
    status: str
    workflow_instance_id: str | None
    documents_total: int
    documents_copied: int
    documents_verified: int
    error_message: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    locked_at: datetime | None
    copied_at: datetime | None
    verified_at: datetime | None
    released_at: datetime | None
    deletion_scheduled_at: datetime | None
    deleted_at: datetime | None

    model_config = {"from_attributes": True}


class TransferStartResult(BaseModel):
    """Response to `POST /transfers` - `pending_approval` (four-eyes principle active)
    returns no `transfer`, since no row has been created yet (the
    actual start only happens when consuming
    `permission.approval.approved`, see `consumer.py`)."""

    status: str
    transfer: TransferOut | None = None
    approval_request_id: str | None = None
