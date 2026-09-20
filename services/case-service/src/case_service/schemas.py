from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CaseCreate(BaseModel):
    name: str
    object_type_id: int | None = None
    attributes: dict = {}
    process_definition_id: int
    created_by: str
    initial_data: dict = {}
    # Draft / pre-registration lifecycle (post-roadmap phase 31 session 2,
    # ADR 0113) - see `Case.registered_at`. Default `False` keeps today's
    # behavior unchanged: every case gets a Vorgangsnummer at creation
    # unless the caller opts into `draft=True`.
    draft: bool = False


class CaseOut(BaseModel):
    id: str
    name: str
    object_type_id: int | None
    attributes: dict
    status: str
    process_definition_id: int
    process_instance_id: str | None
    created_by: str
    created_at: datetime
    closed_at: datetime | None
    archive_after: datetime | None
    archived_at: datetime | None
    vorgangsnummer: str | None
    registered_at: datetime | None

    model_config = {"from_attributes": True}


class CaseRegisterRequest(BaseModel):
    registered_by: str


class CaseNumberConfigIn(BaseModel):
    format: str


class CaseNumberConfigOut(BaseModel):
    format: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseDocumentAdd(BaseModel):
    document_id: str
    added_by: str


class CaseDocumentRemove(BaseModel):
    removed_by: str


class CaseDocumentReferenceOut(BaseModel):
    """Kombiniert die eigene Referenzzeile mit dem zweistufigen Modell aus
    2.3: waehrend die Umlaufmappe offen ist, werden `current_version_number`/
    `document_deleted_at` live aus dem Document Service aufgeloest (siehe
    main.py:list_case_documents); ab Abschluss steht stattdessen der fixierte
    `snapshot_version_number`. Kein `from_attributes`, da die aufgeloesten
    Felder nicht auf dem DB-Modell selbst liegen."""

    document_id: str
    added_by: str
    added_at: datetime
    removed_by: str | None
    removed_at: datetime | None
    snapshot_version_number: int | None
    current_version_number: int | None = None
    document_deleted_at: datetime | None = None
    # Records quarantine (14.2, ADR 0116), since Post-Roadmap Phase 36
    # Session 2 - always live-resolved regardless of case open/closed
    # status (unlike current_version_number/document_deleted_at above,
    # quarantine is not part of the closure-snapshot concept: it's a
    # visibility flag that can change at any time independent of the
    # case's own lifecycle).
    has_active_quarantine: bool = False


class CaseArchiveStatusOut(BaseModel):
    case_id: str
    archive_after: datetime | None
    archived_at: datetime | None


class CaseArchivalConfigIn(BaseModel):
    default_archive_after_days_closed: int | None
    archive_encryption_enabled: bool


class CaseArchivalConfigOut(BaseModel):
    default_archive_after_days_closed: int | None
    archive_encryption_enabled: bool
    updated_at: datetime

    model_config = {"from_attributes": True}


class PseudonymizeAttributeRequest(BaseModel):
    """Trigger pseudonymization of one personal-data attribute (5.2, Phase
    58 Session 1, mirrors document-service's ADR 0156). Manual-only for
    case-service - no automatic retention-expiry trigger, since case-
    service has no `retention_until`/`full_deletion` mechanism at all
    (see `docs/adr/0156-...md`'s Phase 58 Session 1 addendum)."""

    pseudonymized_by: str
    reason: str | None = None


class RevealAttributeRequest(BaseModel):
    revealed_by: str


class PseudonymizedAttributeOut(BaseModel):
    id: str
    case_id: str
    attribute_name: str
    reason: str | None
    pseudonymized_by: str
    pseudonymized_at: datetime
    last_revealed_by: str | None
    last_revealed_at: datetime | None

    model_config = {"from_attributes": True}


class RevealedAttributeOut(BaseModel):
    case_id: str
    attribute_name: str
    value: Any
    reason: str | None
    pseudonymized_by: str
    pseudonymized_at: datetime
