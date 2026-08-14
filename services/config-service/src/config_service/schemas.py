from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# Current schema (7.3: "Versioning the configuration schema itself,
# so an export from an older version can be imported into a newer
# one"). So far there is only this one version - see `migrations.py` for
# the intended extension point once the schema changes in the future.
SCHEMA_VERSION = "1.0"

CATEGORIES = (
    "object_types",
    "workflows",
    "dmn_definitions",
    "business_calendars",
    "roles",
    "approval_config",
    "sensor_config",
    "federation_config",
    "realm_roles",
)


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
    # Classified documents classification level (2.5, P15-S1, multi-level since P17-S2, 14.2) -
    # without this field, a configuration export/import would silently
    # drop the classification.
    classification_level: str | None = None
    layouts: list[ObjectTypeLayoutExport] = []


class WorkflowExport(BaseModel):
    name: str
    bpmn_xml: str


class DmnDefinitionExport(BaseModel):
    """DMN 1.3 decision tables (7.1/7.3, P14-S4) - a separate category instead of
    being part of `WorkflowExport`: a DMN family is versioned independently of
    any individual process definition (see `models.DmnDefinition`) and
    can be referenced by multiple `businessRuleTask`s."""

    name: str
    dmn_xml: str


class BusinessCalendarExport(BaseModel):
    """Regional business calendar for SLA deadline calculation (7.1/7.3,
    P14-S5) - unlike `workflows`/`dmn_definitions`, NO
    versioning pattern (see `workflow_service.models.BusinessCalendar`):
    upsert by `name`, like `roles`/`approval_config`."""

    name: str
    non_working_dates: list[str] = []
    is_default: bool = False


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


class FederationConfigExport(BaseModel):
    """Version compatibility range for federated workflows (7.4, P13-S3) -
    7.4 verbatim: "This compatibility range is part of the already
    versioned configuration schema (7.3)". Before P13-S3 it only lived in
    `workflow-service`'s `Settings` (only changeable via container restart) -
    now a regular 7.3 category like any other."""

    version: str
    min_compatible_peer_version: str


class PackageManifest(BaseModel):
    """Turns a raw `ConfigDocument` into a named, versioned
    **configuration package** (14.1, P17-S1) - e.g. the eGov configuration package
    (14.2). Purely descriptive (name/version/compatibility range/description/
    origin/license), no application logic of its own - `compatibility_range` is
    deliberately a free-form string (e.g. `">=1.0,<2.0"`), analogous to
    `FederationConfigExport.min_compatible_peer_version`: this service does not
    check it itself, it is pure information for the person applying a package
    (see the ADR for P17-S1 for the rationale why no automatic
    enforcement was built in). Optional on `ConfigDocument` - a
    document without `manifest` remains an ordinary 7.3 export/import as before
    P17-S1."""

    name: str
    version: str
    compatibility_range: str
    description: str = ""
    origin: str = ""
    license: str = ""


class ConfigDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    exported_at: datetime
    manifest: PackageManifest | None = None
    object_types: list[ObjectTypeExport] | None = None
    workflows: list[WorkflowExport] | None = None
    dmn_definitions: list[DmnDefinitionExport] | None = None
    business_calendars: list[BusinessCalendarExport] | None = None
    roles: list[RoleExport] | None = None
    approval_config: list[ApprovalConfigExport] | None = None
    sensor_config: SensorConfigExport | None = None
    federation_config: FederationConfigExport | None = None
    # Keycloak realm roles (14.1, P17-S0 finding: `roles` above only covers
    # permission-service's DB-based roles, not Keycloak's separate
    # realm role system - e.g. `dms-poststelle`, 2.5). Plain name list,
    # not a `RoleExport`-like object: a Keycloak realm role has no
    # description/permission list in this project so far (see
    # `bootstrap._ensure_dms_admin_role`).
    realm_roles: list[str] | None = None


class CategoryResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []


class ImportResult(BaseModel):
    schema_version: str
    results: dict[str, CategoryResult]


class ImportActionResult(BaseModel):
    """Like `ForceReleaseResult` (document-service)/`RoleAssignmentActionResult`
    (permission-service) - `POST /config/import` can, since P17-S3, optionally be
    gated via the generic four-eyes mechanism (`config.import`, 14.2
    "Configuration Import"). `POST /config/fleet-import` remains
    deliberately ungated (see its docstring in `main.py`) and continues to
    return the plain `ImportResult`."""

    status: Literal["applied", "pending_approval"]
    result: ImportResult | None = None
    approval_request_id: str | None = None


class CategoryDelta(BaseModel):
    """Result of comparing a single category (7.5, P14-S1) - the
    four classifications 7.5 explicitly requires: only in the base instance,
    only in the compare instance, present in both but differing in content (with
    a detail view per differing attribute), identical. `differing` is keyed
    by display name (base instance raw value, see `compare.py`),
    with `{"base": ..., "compare": ...}` under each differing field."""

    only_in_base: list[str] = []
    only_in_compare: list[str] = []
    differing: dict[str, dict[str, dict[str, Any]]] = {}
    identical: list[str] = []


class CompareRequest(BaseModel):
    """`base` missing -> its own current live export is used as the base instance
    (use case "what would change if I import this document",
    7.5). `ignore_regex` is one pattern per category name, `"*"`
    sets the global default pattern (7.5: "configurable both globally and per
    category")."""

    base: ConfigDocument | None = None
    compare: ConfigDocument
    categories: list[str] | None = None
    ignore_regex: dict[str, str] | None = None


class CompareResult(BaseModel):
    schema_version: str
    base_exported_at: datetime
    compare_exported_at: datetime
    categories: dict[str, CategoryDelta]
