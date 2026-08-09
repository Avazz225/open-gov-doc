"""Übersetzung von `TreeFolder`/`TreeDocument` (dms-connector-sdk) auf die
CMIS-1.1-Objekt-Repräsentation (5.2.11 Succinct Representation of Properties)
- diese Referenzimplementierung liefert AUSSCHLIESSLICH das succinct-Format
  (flache `succinctProperties`, keine typannotierten `properties`-Objekte),
  siehe docs/services/cmis-connector.md "Bewusste Grenzen". Reale CMIS-Clients
  nutzen `succinct=true` ohnehin verbreitet, um Nachrichtengrößen zu
  reduzieren (5.2.11)."""

from datetime import UTC, datetime

from dms_connector_sdk import TreeDocument, TreeFolder

CMIS_VERSION_SUPPORTED = "1.1"


# CMIS datetime -> JSON-Zahl (Millisekunden seit 1970-01-01 UTC), wörtlich aus
# 5.2.4 "Mapping Schema Elements to JSON". `None` bleibt `None` (5.2.7
# "Properties in a value not set state") - z. B. für die Wurzel, deren
# `TreeFolder` bewusst ohne `created_by`/`created_at` konstruiert wird
# (siehe dms_tree_client.resolve_path).
def _to_cmis_datetime(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def folder_properties(folder: TreeFolder, *, path: str) -> dict:
    return {
        "cmis:objectId": folder.id,
        "cmis:objectTypeId": "cmis:folder",
        "cmis:baseTypeId": "cmis:folder",
        "cmis:name": folder.name or "/",
        "cmis:path": path,
        "cmis:parentId": folder.parent_id,
        "cmis:createdBy": folder.created_by,
        "cmis:creationDate": _to_cmis_datetime(folder.created_at),
        # `TreeFolder` führt keinen eigenen `updated_at`-Zeitstempel
        # (folder-service kennt nur `created_at`, siehe FolderOut) - die
        # Erstellungszeit wird bewusst auch als letzte Änderungszeit
        # ausgegeben statt eines erfundenen Werts.
        "cmis:lastModifiedBy": folder.created_by,
        "cmis:lastModificationDate": _to_cmis_datetime(folder.created_at),
        "cmis:changeToken": None,
        "cmis:allowedChildObjectTypeIds": ["cmis:folder", "cmis:document"],
    }


def document_properties(
    document: TreeDocument, *, is_checked_out: bool, checked_out_by: str | None
) -> dict:
    return {
        "cmis:objectId": document.id,
        "cmis:objectTypeId": "cmis:document",
        "cmis:baseTypeId": "cmis:document",
        "cmis:name": document.title,
        "cmis:createdBy": document.created_by,
        "cmis:creationDate": _to_cmis_datetime(document.created_at),
        "cmis:lastModifiedBy": document.created_by,
        "cmis:lastModificationDate": _to_cmis_datetime(document.updated_at),
        "cmis:changeToken": None,
        "cmis:contentStreamLength": document.size_bytes,
        "cmis:contentStreamMimeType": document.content_type,
        "cmis:contentStreamFileName": document.title,
        "cmis:versionLabel": str(document.current_version_number),
        # `document-service`s Checkin-Historie liefert immer die jeweils
        # aktuellste Version zurück (kein Konzept nicht-aktueller,
        # gleichzeitig sichtbarer Versionen) - daher immer `true`, siehe
        # docs/services/cmis-connector.md "Bewusste Grenzen".
        "cmis:isLatestVersion": True,
        "cmis:isMajorVersion": True,
        "cmis:isLatestMajorVersion": True,
        "cmis:isImmutable": False,
        "cmis:isPrivateWorkingCopy": False,
        "cmis:isVersionSeriesCheckedOut": is_checked_out,
        "cmis:versionSeriesCheckedOutBy": checked_out_by,
        # Kein separates PWC-Objekt (siehe `main.py` checkOut) - die
        # Working-Copy-ID ist bewusst identisch zur Original-Dokument-ID.
        "cmis:versionSeriesCheckedOutId": document.id if is_checked_out else None,
    }
