"""Translation of `TreeFolder`/`TreeDocument` (dms-connector-sdk) to the
CMIS 1.1 object representation (5.2.11 Succinct Representation of Properties)
- this reference implementation delivers EXCLUSIVELY the succinct format
  (flat `succinctProperties`, no type-annotated `properties` objects),
  see docs/services/cmis-connector.md "Deliberate Limitations". Real CMIS clients
  commonly use `succinct=true` anyway to reduce message sizes (5.2.11)."""

from datetime import UTC, datetime

from dms_connector_sdk import TreeDocument, TreeFolder

CMIS_VERSION_SUPPORTED = "1.1"


# CMIS datetime -> JSON number (milliseconds since 1970-01-01 UTC), verbatim from
# 5.2.4 "Mapping Schema Elements to JSON". `None` stays `None` (5.2.7
# "Properties in a value not set state") - e.g. for the root, whose
# `TreeFolder` is deliberately constructed without `created_by`/`created_at`
# (see dms_tree_client.resolve_path).
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
        # `TreeFolder` does not carry its own `updated_at` timestamp
        # (folder-service only knows `created_at`, see FolderOut) - the
        # creation time is deliberately also output as the last modification
        # time instead of a made-up value.
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
        # `document-service`'s checkin history always returns the respective
        # latest version (no concept of non-current, simultaneously
        # visible versions) - hence always `true`, see
        # docs/services/cmis-connector.md "Deliberate Limitations".
        "cmis:isLatestVersion": True,
        "cmis:isMajorVersion": True,
        "cmis:isLatestMajorVersion": True,
        "cmis:isImmutable": False,
        "cmis:isPrivateWorkingCopy": False,
        "cmis:isVersionSeriesCheckedOut": is_checked_out,
        "cmis:versionSeriesCheckedOutBy": checked_out_by,
        # No separate PWC object (see `main.py` checkOut) - the
        # working copy ID is deliberately identical to the original document ID.
        "cmis:versionSeriesCheckedOutId": document.id if is_checked_out else None,
    }
