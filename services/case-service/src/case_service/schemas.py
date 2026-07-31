from datetime import datetime

from pydantic import BaseModel


class CaseCreate(BaseModel):
    name: str
    object_type_id: int | None = None
    attributes: dict = {}
    process_definition_id: int
    created_by: str
    initial_data: dict = {}


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
