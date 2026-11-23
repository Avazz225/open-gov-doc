from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class LayoutPurpose(StrEnum):
    """Usage purpose of a form layout (2.2b) - the same layout format
    governs all three, but as separately storable/overridable rows."""

    display = "display"
    search = "search"
    upload = "upload"


SignatureLevel = Literal["ses", "aes", "qes"]

# Classified-documents classification (2.5, multi-level since P17-S2 - 14.2):
# the four common German classification levels, taken verbatim from the
# concept text.
ClassificationLevel = Literal["VS-NfD", "VS-VERTRAULICH", "GEHEIM", "STRENG GEHEIM"]


class ObjectTypeCreate(BaseModel):
    name: str
    applies_to: Literal["document", "folder"]
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    status_transitions: list[dict] = []
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None
    required_signature_level: SignatureLevel | None = None
    default_retention_days: int | None = None
    deletion_reason_required_override: bool | None = None
    default_archive_after_days: int | None = None
    archive_encryption_enabled: bool = False
    classification_level: ClassificationLevel | None = None
    # P90-S5 (ADR 0258), document types only.
    metadata_mappings: list[dict] = []
    # Enforced filing structure, parent side (P91-S2a, ADR 0255) - folder
    # classes only.
    allowed_child_folder_types: list[str] | None = None
    allowed_document_types: list[str] | None = None
    default_child_folder_type: str | None = None
    default_document_type: str | None = None
    children_from_template_only: bool = False


class ObjectTypeUpdate(BaseModel):
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    status_transitions: list[dict] = []
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None
    required_signature_level: SignatureLevel | None = None
    default_retention_days: int | None = None
    deletion_reason_required_override: bool | None = None
    default_archive_after_days: int | None = None
    archive_encryption_enabled: bool = False
    classification_level: ClassificationLevel | None = None
    # P90-S5 (ADR 0258). `None` = leave unchanged: this update replaces every
    # other field, and a client that predates this one (or forgets it) must
    # not silently clear the mappings - the way status transitions were lost
    # until P88-S3.
    metadata_mappings: list[dict] | None = None
    # P91-S2a (ADR 0255). Like `metadata_mappings`, a field left out keeps
    # its stored value (checked via `model_fields_set`, so an explicit `null`
    # still opens a list again) - clients that predate these fields must not
    # reset a structure.
    allowed_child_folder_types: list[str] | None = None
    allowed_document_types: list[str] | None = None
    default_child_folder_type: str | None = None
    default_document_type: str | None = None
    children_from_template_only: bool | None = None


class ObjectTypeOut(BaseModel):
    id: int
    name: str
    applies_to: str
    attributes: list[dict]
    naming_constraints: dict | None
    conditions: list[dict]
    allowed_parent_types: list[str] | None
    status_transitions: list[dict]
    icon: str | None
    kennzeichen_format: str | None
    kennzeichen_display_override: bool | None
    required_signature_level: str | None
    default_retention_days: int | None
    deletion_reason_required_override: bool | None
    default_archive_after_days: int | None
    archive_encryption_enabled: bool
    classification_level: str | None
    metadata_mappings: list[dict] = []
    allowed_child_folder_types: list[str] | None = None
    allowed_document_types: list[str] | None = None
    default_child_folder_type: str | None = None
    default_document_type: str | None = None
    children_from_template_only: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KennzeichenOut(BaseModel):
    kennzeichen: str


class NextKennzeichenRequest(BaseModel):
    """Attribute values of the document being created (since P17-S2, 14.2) -
    basis for attribute-based reference number placeholders like
    `{Federfuehrung}` (every placeholder that is not a date/counter
    placeholder is interpreted as an attribute name, see
    repository._render_kennzeichen). Empty/omitted has no effect for a
    purely date-/counter-based format string as before (P5e-S1)."""

    attributes: dict = {}


class KennzeichenConfigIn(BaseModel):
    show_before_filename: bool


class KennzeichenConfigOut(KennzeichenConfigIn):
    updated_at: datetime

    model_config = {"from_attributes": True}


class ValidateRequest(BaseModel):
    name: str
    attributes: dict = {}
    # Placement information resolved by the caller (Folder/Document Service)
    # (2.2a) - deliberately transmitted as object_type_id/root flag instead
    # of an already-resolved name, so that Object-Type Service serves as the
    # single source of name resolution (no extra roundtrip needed by the
    # caller).
    parent_object_type_id: int | None = None
    parent_is_root: bool = False
    # Status-transition validation (4.5/7.1, Phase 45 Session 4) - both
    # unset (the default) means "not a status transition", identical
    # behavior to before this field existed.
    from_status: str | None = None
    to_status: str | None = None


StructureEnforcement = Literal["off", "report", "enforce"]


class StructureConfigIn(BaseModel):
    """Partial update: fields left out keep their value (P91-S2a)."""

    root_allowed_folder_types: list[str] | None = None
    root_allowed_document_types: list[str] | None = None
    enforcement: StructureEnforcement | None = None


class StructureConfigOut(BaseModel):
    root_allowed_folder_types: list[str] | None
    root_allowed_document_types: list[str] | None
    enforcement: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class StructureCheckRequest(BaseModel):
    """One new child below a parent (P91-S2a, ADR 0255): folder-service and
    document-service ask this before creating or moving."""

    parent_object_type_id: int | None = None
    parent_is_root: bool = False
    child_kind: Literal["folder", "document"]
    child_object_type_id: int | None = None
    via_template: bool = False


class StructureCheckResult(BaseModel):
    enforcement: str
    errors: list[str]
    # Set when the child has no type and the parent names a default that is
    # itself allowed there; the caller creates the child with this type.
    default_object_type_id: int | None = None


class AllowedChildrenOut(BaseModel):
    enforcement: str
    folder_types: list[dict]
    document_types: list[dict]
    untyped_folders: bool
    untyped_documents: bool
    children_from_template_only: bool
    default_child_folder_type: str | None = None
    default_document_type: str | None = None


class ValidateResult(BaseModel):
    valid: bool
    errors: list[str]


class LayoutField(BaseModel):
    attribute: str
    label: str
    required: bool = False


class LayoutRow(BaseModel):
    columns: list[LayoutField]


class LayoutIn(BaseModel):
    rows: list[LayoutRow] = []
    responsive_breakpoint_px: int = 600


class LayoutOut(LayoutIn):
    # False = generated smart layout (not persisted), True = deviation
    # explicitly saved via PUT (see ADR 0014).
    is_custom: bool
