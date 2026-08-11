from datetime import datetime
from typing import Any, Literal

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
    # Verschlusssachen-Einstufung (2.5, P15-S1, mehrstufig seit P17-S2, 14.2) -
    # ohne dieses Feld würde ein Konfigurationsexport/-import die Einstufung
    # stillschweigend fallen lassen.
    classification_level: str | None = None
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


class BusinessCalendarExport(BaseModel):
    """Regionaler Geschäftskalender für die SLA-Fristberechnung (7.1/7.3,
    P14-S5) - anders als `workflows`/`dmn_definitions` KEIN
    Versionierungsmuster (siehe `workflow_service.models.BusinessCalendar`):
    Upsert per `name`, wie `roles`/`approval_config`."""

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
    """Versionskompatibilitätsspanne für föderierte Workflows (7.4, P13-S3) -
    7.4 wörtlich: "Diese Kompatibilitätsspanne ist Teil des ohnehin schon
    versionierten Konfigurationsschemas (7.3)". Vor P13-S3 lebte sie nur in
    `workflow-service`s `Settings` (nur per Container-Neustart änderbar) -
    jetzt reguläre 7.3-Kategorie wie jede andere."""

    version: str
    min_compatible_peer_version: str


class PackageManifest(BaseModel):
    """Macht aus einem rohen `ConfigDocument` ein benanntes, versioniertes
    **Konfigurationspaket** (14.1, P17-S1) - z. B. das eGov-Konfigurationspaket
    (14.2). Rein beschreibend (Name/Version/Kompatibilitätsspanne/Beschreibung/
    Herkunft/Lizenz), keine eigene Anwendungslogik - `compatibility_range` ist
    bewusst ein freier String (z. B. `">=1.0,<2.0"`), analog zu
    `FederationConfigExport.min_compatible_peer_version`: dieser Service prüft
    sie nicht selbst, sie ist reine Information für die Person, die ein Paket
    anwendet (siehe ADR zu P17-S1 für die Begründung, warum keine automatische
    Durchsetzung eingebaut wurde). Optional auf `ConfigDocument` - ein
    Dokument ohne `manifest` bleibt ein gewöhnlicher 7.3-Export/Import wie vor
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
    # Keycloak-Realm-Rollen (14.1, P17-S0-Befund: `roles` oben deckt nur
    # permission-services DB-basierte Rollen ab, nicht Keycloaks separates
    # Realm-Rollen-System - z. B. `dms-poststelle`, 2.5). Reine Namensliste,
    # kein `RoleExport`-artiges Objekt: eine Keycloak-Realm-Rolle hat in
    # diesem Projekt bislang keine Beschreibung/Berechtigungsliste (siehe
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
    """Wie `ForceReleaseResult` (document-service)/`RoleAssignmentActionResult`
    (permission-service) - `POST /config/import` kann seit P17-S3 optional per
    generischem Vier-Augen-Mechanismus gegated sein (`config.import`, 14.2
    "Konfigurationsimport"). `POST /config/fleet-import` bleibt bewusst
    ungegated (siehe dortiger Docstring in `main.py`) und liefert weiterhin
    das einfache `ImportResult`."""

    status: Literal["applied", "pending_approval"]
    result: ImportResult | None = None
    approval_request_id: str | None = None


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
