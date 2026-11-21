from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CaseCreate(BaseModel):
    name: str
    object_type_id: int | None = None
    attributes: dict = {}
    process_definition_id: int
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
    # P89-S1 (ADR 0235)
    retention_until: datetime | None = None
    full_deletion: bool = False
    destroyed_at: datetime | None = None

    model_config = {"from_attributes": True}


class CaseRegisterRequest(BaseModel):
    """Empty since P85-S5 (ADR 0240): the actor is the request principal;
    the body is kept so existing clients that still send one keep working."""


class CaseNumberConfigIn(BaseModel):
    format: str


class CaseNumberConfigOut(BaseModel):
    format: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseDocumentAdd(BaseModel):
    document_id: str


class CaseDocumentRemove(BaseModel):
    """Empty since P85-S5 (ADR 0240): the actor is the request principal;
    the body is kept so existing clients that still send one keep working."""


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
    """The two P89-S1 fields are optional: a client that doesn't send them
    (admin-ui before P89-S2) keeps their stored values instead of resetting
    them - only fields present in the request are written."""

    default_archive_after_days_closed: int | None
    archive_encryption_enabled: bool
    default_retention_days_after_archived: int | None = Field(default=None, ge=0)
    default_full_deletion: bool = False


class CaseArchivalConfigOut(BaseModel):
    default_archive_after_days_closed: int | None
    archive_encryption_enabled: bool
    default_retention_days_after_archived: int | None = None
    default_full_deletion: bool = False
    updated_at: datetime

    model_config = {"from_attributes": True}


class PseudonymizeAttributeRequest(BaseModel):
    """Trigger pseudonymization of one personal-data attribute (5.2, Phase
    58 Session 1, mirrors document-service's ADR 0156). Manual-only for
    case-service - no automatic retention-expiry trigger, since case-
    service has no `retention_until`/`full_deletion` mechanism at all
    (see `docs/adr/0156-...md`'s Phase 58 Session 1 addendum)."""

    reason: str | None = None


class RevealAttributeRequest(BaseModel):
    """Empty since P85-S5 (ADR 0240): the actor is the request principal;
    the body is kept so existing clients that still send one keep working."""


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


class CaseDestroyedOut(BaseModel):
    """Result of `POST /cases/{id}/destroy` (P89-S1, ADR 0235) - what the
    deletion register (P89-S2) records about a case that no longer has a
    name or contents."""

    case_id: str
    vorgangsnummer: str | None
    mode: Literal["tombstone", "deleted"]
    destroyed_at: datetime
    document_reference_count: int


class CaseDeletionRegisterEntryOut(BaseModel):
    """Deletion register for cases (P89-S2) - mirrors document-/folder-
    service's `DeletionRegisterEntryOut`, plus the Vorgangsnummer, the
    destruction mode and how many document references went."""

    id: str
    case_id: str
    vorgangsnummer: str | None
    trigger: Literal["retention_expiry", "restore_reconciliation"]
    mode: Literal["tombstone", "deleted"]
    document_reference_count: int
    reason: str | None
    triggered_by: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ReconcileRestoreDeletionRequest(BaseModel):
    """Deletion reconciliation after a restore (10.4, P89-S2) - same shape
    as document-/folder-service's."""

    original_entry_id: str
    reason: str | None = None
