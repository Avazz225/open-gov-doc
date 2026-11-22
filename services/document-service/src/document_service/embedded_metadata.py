"""Embedded-metadata extraction on upload (P90-S5, ADR 0258).

Reads a file's own properties into one vocabulary shared by every format
(object-type-service's `EMBEDDED_METADATA_SOURCES`):
title, subject, author, keywords, description, category, language, created,
modified, last_modified_by, application.

- **PDF:** the document information dictionary, and the XMP packet where the
  info dictionary is empty (Dublin Core / xmp / pdf namespaces).
- **Office Open XML** (docx/xlsx/pptx and their macro/template variants):
  `docProps/core.xml` and `docProps/app.xml`.
- **OpenDocument** (odt/ods/odp): `meta.xml`.

Everything here is pure and defensive. The input is an untrusted upload, so:
- XML goes through defusedxml (no entities, no external references);
- a zip member is read only up to `MAX_MEMBER_BYTES`;
- any parsing failure yields "no metadata" instead of an error. An upload
  never fails because its properties couldn't be read.

`apply_mappings` merges the extracted values into the attributes the
uploader sent, following the object type's mappings."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime

from defusedxml import ElementTree

MAX_MEMBER_BYTES = 1024 * 1024
MAX_VALUE_LENGTH = 1000

# Sniffed types that are worth opening as a package; plain zips are checked
# by their layout as well (see `extract`).
_OOXML_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_ODF_TYPES = {
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}

_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "pdf": "http://ns.adobe.com/pdf/1.3/",
}


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:MAX_VALUE_LENGTH] if text else None


def _iso_date(value: str | None) -> str | None:
    """A date from PDF (`D:20240131093000+01'00'`) or ISO 8601 as `YYYY-MM-DD`."""
    if not value:
        return None
    text = value.strip()
    pdf = re.match(r"^D:(\d{4})(\d{2})?(\d{2})?", text)
    if pdf:
        year, month, day = pdf.group(1), pdf.group(2) or "01", pdf.group(3) or "01"
        try:
            return datetime(int(year), int(month), int(day), tzinfo=UTC).date().isoformat()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        return None
    with archive.open(info) as member:
        return member.read(MAX_MEMBER_BYTES + 1)[:MAX_MEMBER_BYTES]


def _text(root, path: str) -> str | None:
    node = root.find(path, _NS)
    return _clean(node.text) if node is not None else None


def _from_ooxml(data: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        core = _read_member(archive, "docProps/core.xml")
        if core:
            root = ElementTree.fromstring(core)
            result.update(
                {
                    "title": _text(root, "dc:title"),
                    "subject": _text(root, "dc:subject"),
                    "author": _text(root, "dc:creator"),
                    "keywords": _text(root, "cp:keywords"),
                    "description": _text(root, "dc:description"),
                    "category": _text(root, "cp:category"),
                    "language": _text(root, "dc:language"),
                    "last_modified_by": _text(root, "cp:lastModifiedBy"),
                    "created": _iso_date(_text(root, "dcterms:created")),
                    "modified": _iso_date(_text(root, "dcterms:modified")),
                }
            )
        app = _read_member(archive, "docProps/app.xml")
        if app:
            result["application"] = _text(ElementTree.fromstring(app), "ep:Application")
    return result


def _from_odf(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        meta = _read_member(archive, "meta.xml")
    if not meta:
        return {}
    root = ElementTree.fromstring(meta)
    base = "office:meta/"
    keywords = [
        _clean(node.text) for node in root.findall(base + "meta:keyword", _NS) if _clean(node.text)
    ]
    return {
        "title": _text(root, base + "dc:title"),
        "subject": _text(root, base + "dc:subject"),
        "author": _text(root, base + "meta:initial-creator") or _text(root, base + "dc:creator"),
        "keywords": ", ".join(keywords) or None,
        "description": _text(root, base + "dc:description"),
        "language": _text(root, base + "dc:language"),
        "last_modified_by": _text(root, base + "dc:creator"),
        "created": _iso_date(_text(root, base + "meta:creation-date")),
        "modified": _iso_date(_text(root, base + "dc:date")),
        "application": _text(root, base + "meta:generator"),
    }


def _xmp_values(reader) -> dict[str, str]:
    """XMP is only a fallback for fields the info dictionary leaves empty."""
    try:
        xmp = reader.xmp_metadata
    except Exception:  # noqa: BLE001 - a broken packet is no packet
        return {}
    if xmp is None:
        return {}

    def first(value):
        if isinstance(value, dict):  # language alternatives: {"x-default": "..."}
            value = value.get("x-default") or next(iter(value.values()), None)
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value if v)
        return _clean(value)

    def safe(getter):
        try:
            return getter()
        except Exception:  # noqa: BLE001
            return None

    created = safe(lambda: xmp.xmp_create_date)
    modified = safe(lambda: xmp.xmp_modify_date)
    return {
        "title": first(safe(lambda: xmp.dc_title)),
        "subject": first(safe(lambda: xmp.dc_description)),
        "author": first(safe(lambda: xmp.dc_creator)),
        "keywords": _clean(safe(lambda: xmp.pdf_keywords)) or first(safe(lambda: xmp.dc_subject)),
        "application": _clean(safe(lambda: xmp.xmp_creator_tool)),
        "created": created.date().isoformat() if isinstance(created, datetime) else None,
        "modified": modified.date().isoformat() if isinstance(modified, datetime) else None,
    }


def _from_pdf(data: bytes) -> dict[str, str]:
    from pypdf import PdfReader  # local: only PDFs pay the import

    reader = PdfReader(io.BytesIO(data))
    info = reader.metadata or {}
    result = {
        "title": _clean(info.get("/Title")),
        "subject": _clean(info.get("/Subject")),
        "author": _clean(info.get("/Author")),
        "keywords": _clean(info.get("/Keywords")),
        "application": _clean(info.get("/Creator")) or _clean(info.get("/Producer")),
        "created": _iso_date(_clean(info.get("/CreationDate"))),
        "modified": _iso_date(_clean(info.get("/ModDate"))),
    }
    for key, value in _xmp_values(reader).items():
        if value and not result.get(key):
            result[key] = value
    return result


def extract(data: bytes, content_type: str | None) -> dict[str, str]:
    """The file's properties in the shared vocabulary, empty ones left out.
    Never raises: an unreadable file has no metadata."""
    try:
        if content_type == "application/pdf" or data[:5] == b"%PDF-":
            values = _from_pdf(data)
        elif content_type in _OOXML_TYPES | _ODF_TYPES or data[:4] == b"PK\x03\x04":
            # libmagic reports some Office files as plain `application/zip`,
            # so the package layout decides, not the sniffed type.
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
            if "docProps/core.xml" in names or "docProps/app.xml" in names:
                values = _from_ooxml(data)
            elif "meta.xml" in names:
                values = _from_odf(data)
            else:
                return {}
        else:
            return {}
    except Exception:  # noqa: BLE001 - metadata is a convenience, never a reason to fail
        return {}
    return {key: value for key, value in values.items() if value}


def apply_mappings(
    attributes: dict, extracted: dict[str, str], mappings: list[dict]
) -> tuple[dict, list[str]]:
    """The uploader's attributes plus mapped values: `if_empty` fills only an
    attribute the uploader left empty, `always` overwrites. Returns the merged
    attributes and the names that came from the file."""
    merged = dict(attributes)
    applied: list[str] = []
    for mapping in mappings or []:
        value = extracted.get(mapping.get("source", ""))
        attribute = mapping.get("attribute")
        if not value or not attribute:
            continue
        if mapping.get("overwrite", "if_empty") == "if_empty" and merged.get(attribute) not in (
            None,
            "",
        ):
            continue
        merged[attribute] = value
        applied.append(attribute)
    return merged, applied
