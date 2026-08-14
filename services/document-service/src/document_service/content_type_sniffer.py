import magic

# No technical MIME value for empty uploads (`magic` would otherwise
# return "application/x-empty") - consistent with the existing fallback in
# main.py's download endpoints.
_EMPTY_CONTENT_TYPE = "application/octet-stream"


def sniff_content_type(data: bytes) -> str:
    """Determines the content type server-side from the actual magic
    bytes of the upload (P5d-S1) instead of from the unchecked
    `file.content_type` header sent by the client - fixes incorrect/generic
    content types that arrive depending on browser/operating system, e.g.
    for .txt/.json (see PROGRESS.md, user feedback after phase 5c)."""
    if not data:
        return _EMPTY_CONTENT_TYPE
    return magic.from_buffer(data, mime=True)
