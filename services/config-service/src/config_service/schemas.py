from datetime import datetime

from pydantic import BaseModel

# Aktuelles Schema (7.3: "Versionierung des Konfigurationsschemas selbst,
# damit Export aus einer älteren Version in eine neuere importiert werden
# kann"). Es gibt bislang nur diese eine Version - siehe `migrations.py` für
# den vorgesehenen Erweiterungspunkt, sobald sich das Schema künftig ändert.
SCHEMA_VERSION = "1.0"

CATEGORIES = ("object_types", "workflows", "roles", "approval_config", "sensor_config")


class ObjectTypeLayoutExport(BaseModel):
    purpose: str
    rows: list[dict] = []
    responsive_breakpoint_px: int = 600


class ObjectTypeExport(BaseModel):
    name: str
    applies_to: str
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None
    required_signature_level: str | None = None
    default_retention_days: int | None = None
    deletion_reason_required_override: bool | None = None
    default_archive_after_days: int | None = None
    archive_encryption_enabled: bool = False
    layouts: list[ObjectTypeLayoutExport] = []


class WorkflowExport(BaseModel):
    name: str
    bpmn_xml: str


class RoleExport(BaseModel):
    name: str
    description: str
    permissions: list[str]


class ApprovalConfigExport(BaseModel):
    action_type: str
    requires_approval: bool


class SensorConfigExport(BaseModel):
    global_default: bool
    overrides: dict[str, bool]


class ConfigDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    exported_at: datetime
    object_types: list[ObjectTypeExport] | None = None
    workflows: list[WorkflowExport] | None = None
    roles: list[RoleExport] | None = None
    approval_config: list[ApprovalConfigExport] | None = None
    sensor_config: SensorConfigExport | None = None


class CategoryResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []


class ImportResult(BaseModel):
    schema_version: str
    results: dict[str, CategoryResult]
