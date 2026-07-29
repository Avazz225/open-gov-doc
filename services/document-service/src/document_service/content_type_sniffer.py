import magic

# Kein technischer MIME-Wert für leere Uploads (`magic` liefert sonst
# "application/x-empty") - konsistent mit dem bestehenden Fallback in
# main.py's Download-Endpunkten.
_EMPTY_CONTENT_TYPE = "application/octet-stream"


def sniff_content_type(data: bytes) -> str:
    """Ermittelt den Content-Type serverseitig aus den tatsächlichen
    Magic-Bytes des Uploads (P5d-S1) statt aus dem ungeprüft vom Client
    gesendeten `file.content_type`-Header - behebt falsche/generische
    Content-Types, die je nach Browser/Betriebssystem z. B. bei .txt/.json
    ankommen (siehe PROGRESS.md, Nutzer-Feedback nach Phase 5c)."""
    if not data:
        return _EMPTY_CONTENT_TYPE
    return magic.from_buffer(data, mime=True)
