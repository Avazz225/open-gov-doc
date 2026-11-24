import base64
import binascii

import magic

# No technical MIME value for empty uploads (`magic` would otherwise
# return "application/x-empty") - consistent with the existing fallback in
# main.py's download endpoints.
_EMPTY_CONTENT_TYPE = "application/octet-stream"

# CMS signatures (P92-S4, ADR 0248 Decision 2): libmagic reports DER
# PKCS#7/CMS as `application/octet-stream`, so they are recognised here.
# Enveloping (`.p7m`, the signed content inside) and detached (`.p7s`,
# signature only) get the MIME types RFC 5751 uses for them.
CMS_ENVELOPING = "application/pkcs7-mime"
CMS_DETACHED = "application/pkcs7-signature"
# id-signedData, 1.2.840.113549.1.7.2, as DER (tag, length, value).
_SIGNED_DATA_OID = bytes.fromhex("06092a864886f70d010702")
_PEM_HEADERS = (b"-----BEGIN PKCS7-----", b"-----BEGIN CMS-----")


def _tlv(data: bytes, offset: int) -> tuple[int, int, int]:
    """(tag, start of value, end of value) of the DER element at `offset`.
    Raises ValueError for anything this small reader doesn't handle (long
    tags, indefinite lengths)."""
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise ValueError("long tag")
    length = data[offset + 1]
    start = offset + 2
    if length & 0x80:
        count = length & 0x7F
        if count == 0 or count > 4:
            raise ValueError("indefinite or oversized length")
        length = int.from_bytes(data[start : start + count], "big")
        start += count
    end = start + length
    if end > len(data):
        raise ValueError("truncated")
    return tag, start, end


def _cms_kind(der: bytes) -> str | None:
    """`CMS_ENVELOPING`/`CMS_DETACHED` for a DER CMS SignedData, else None.
    Walks ContentInfo -> SignedData -> encapContentInfo and checks whether
    the optional eContent is present."""
    try:
        tag, start, end = _tlv(der, 0)
        if tag != 0x30 or not der[start:end].startswith(_SIGNED_DATA_OID):
            return None
        # [0] EXPLICIT SignedData
        tag, start, end = _tlv(der, start + len(_SIGNED_DATA_OID))
        if tag != 0xA0:
            return None
        tag, sd_start, sd_end = _tlv(der, start)  # SignedData SEQUENCE
        _, _, cursor = _tlv(der, sd_start)  # version
        _, _, cursor = _tlv(der, cursor)  # digestAlgorithms
        tag, eci_start, eci_end = _tlv(der, cursor)  # encapContentInfo
        if tag != 0x30:
            return None
        _, _, cursor = _tlv(der, eci_start)  # eContentType
        return CMS_ENVELOPING if cursor < eci_end else CMS_DETACHED
    except (ValueError, IndexError):
        # A CMS this reader can't walk (e.g. BER with indefinite lengths)
        # still is one; enveloping is the more common kind.
        return CMS_ENVELOPING if _SIGNED_DATA_OID in der[:32] else None


def _pem_cms(data: bytes) -> bytes | None:
    head = data[:64].lstrip()
    if not head.startswith(_PEM_HEADERS):
        return None
    body = b"".join(line for line in data.splitlines() if line and not line.startswith(b"-----"))
    try:
        return base64.b64decode(body, validate=False)
    except (binascii.Error, ValueError):
        return None


def sniff_content_type(data: bytes) -> str:
    """Determines the content type server-side from the actual magic
    bytes of the upload (P5d-S1) instead of from the unchecked
    `file.content_type` header sent by the client - fixes incorrect/generic
    content types that arrive depending on browser/operating system, e.g.
    for .txt/.json (see PROGRESS.md, user feedback after phase 5c)."""
    if not data:
        return _EMPTY_CONTENT_TYPE
    if data[:1] == b"\x30":
        kind = _cms_kind(data)
        if kind:
            return kind
    pem = _pem_cms(data)
    if pem is not None:
        kind = _cms_kind(pem)
        if kind:
            return kind
    return magic.from_buffer(data, mime=True)
