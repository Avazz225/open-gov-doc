from datetime import datetime

from pydantic import BaseModel


class PairedInstallationCreate(BaseModel):
    display_name: str
    base_url: str
    # Leer lassen, wenn diese Installation die PAARUNG initiiert (ein neuer
    # Key wird generiert und einmalig zurückgegeben) - gesetzt, wenn hier der
    # von der Gegenseite bereits ausgegebene Key eingetragen wird (siehe
    # docs/services/migration-service.md "Paarung").
    api_key: str | None = None


class PairedInstallationOut(BaseModel):
    id: str
    display_name: str
    base_url: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PairedInstallationCreateOut(PairedInstallationOut):
    # Nur in der Anlage-Antwort enthalten (nie in GET/List) - identisches
    # "einmalig zurückgeben, nie wieder aussenden"-Prinzip wie
    # federation-hub-service, auch wenn hier zusätzlich intern gespeichert.
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
    """Antwort auf `POST /transfers` - `pending_approval` (Vier-Augen aktiv)
    liefert keinen `transfer`, da noch keine Zeile angelegt wurde (der
    eigentliche Start passiert erst beim Konsumieren von
    `permission.approval.approved`, siehe `consumer.py`)."""

    status: str
    transfer: TransferOut | None = None
    approval_request_id: str | None = None
