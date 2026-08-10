from datetime import datetime
from typing import Any

from pydantic import BaseModel

# Aktuelles Schema (7.3: "Versionierung des Konfigurationsschemas selbst,
# damit Export aus einer älteren Version in eine neuere importiert werden
# kann"). Es gibt bislang nur diese eine Version - siehe `migrations.py` für
# den vorgesehenen Erweiterungspunkt, sobald sich das Schema künftig ändert.
SCHEMA_VERSION = "1.0"

CATEGORIES = (
    "object_types",
    "workflows",
    "dmn_definitions",
    "roles",
    "approval_config",
    "sensor_config",
    "federation_config",
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
    layouts: list[ObjectTypeLayoutExport] = []


class WorkflowExport(BaseModel):
    name: str
    bpmn_xml: str


class DmnDefinitionExport(BaseModel):
    """DMN-1.3-Entscheidungstabellen (7.1/7.3, P14-S4) - eigene Kategorie statt
    Teil von `WorkflowExport`: eine DMN-Familie ist unabhängig von jeder
    einzelnen Prozessdefinition versioniert (siehe `models.DmnDefinition`) und
    kann von mehreren `businessRuleTask`s referenziert werden."""

    name: str
    dmn_xml: str


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
    """Versionskompatibilitätsspanne für föderierte Workflows (7.4, P13-S3) -
    7.4 wörtlich: "Diese Kompatibilitätsspanne ist Teil des ohnehin schon
    versionierten Konfigurationsschemas (7.3)". Vor P13-S3 lebte sie nur in
    `workflow-service`s `Settings` (nur per Container-Neustart änderbar) -
    jetzt reguläre 7.3-Kategorie wie jede andere."""

    version: str
    min_compatible_peer_version: str


class ConfigDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    exported_at: datetime
    object_types: list[ObjectTypeExport] | None = None
    workflows: list[WorkflowExport] | None = None
    dmn_definitions: list[DmnDefinitionExport] | None = None
    roles: list[RoleExport] | None = None
    approval_config: list[ApprovalConfigExport] | None = None
    sensor_config: SensorConfigExport | None = None
    federation_config: FederationConfigExport | None = None


class CategoryResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = []


class ImportResult(BaseModel):
    schema_version: str
    results: dict[str, CategoryResult]


class CategoryDelta(BaseModel):
    """Ergebnis des Vergleichs einer einzelnen Kategorie (7.5, P14-S1) - die
    vier Kategorisierungen, die 7.5 wörtlich verlangt: nur in der Basisinstanz,
    nur in der Vergleichsinstanz, in beiden aber inhaltlich abweichend (mit
    Detailanzeige je abweichendem Attribut), identisch. `differing` ist nach
    Anzeigenamen (Basisinstanz-Rohwert, siehe `compare.py`) geschlüsselt,
    darunter je abweichendem Feld `{"base": ..., "compare": ...}`."""

    only_in_base: list[str] = []
    only_in_compare: list[str] = []
    differing: dict[str, dict[str, dict[str, Any]]] = {}
    identical: list[str] = []


class CompareRequest(BaseModel):
    """`base` fehlt -> der eigene aktuelle Live-Export wird als Basisinstanz
    verwendet (Anwendungsfall "was würde sich ändern, wenn ich dieses Dokument
    importiere", 7.5). `ignore_regex` ist je Kategorie-Name ein Muster, `"*"`
    setzt das globale Default-Muster (7.5: "sowohl global als auch je
    Kategorie konfigurierbar")."""

    base: ConfigDocument | None = None
    compare: ConfigDocument
    categories: list[str] | None = None
    ignore_regex: dict[str, str] | None = None


class CompareResult(BaseModel):
    schema_version: str
    base_exported_at: datetime
    compare_exported_at: datetime
    categories: dict[str, CategoryDelta]
