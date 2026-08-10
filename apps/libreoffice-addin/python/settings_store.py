"""Zustand des Add-ins (P14-S9): Anmeldesitzung + Dokument-Verknüpfung.

Zwei getrennte Speicherorte, bewusst analog zu apps/office-addin (P14-S8):

- **Sitzung** (Token): eine kleine JSON-Datei im Home-Verzeichnis des
  Nutzers (`~/.ogdoc/session.json`) - kein Äquivalent zu `localStorage` in
  einer Desktop-Erweiterung, eine Datei ist der naheliegende Ersatz. Bewusst
  NICHT über UNOs `PathSubstitution`-Dienst (bräuchte einen UNO-Kontext, hier
  unnötige Komplexität) - `pathlib.Path.home()` ist plattformübergreifend
  bereits ausreichend portabel (Windows/Linux/Mac).
- **Dokument-Verknüpfung** (welches OG-Doc-Dokument gehört zu dieser
  geöffneten Datei): `document.getDocumentProperties().UserDefinedProperties`
  - UNOs Äquivalent zu Office.js' `document.settings`, wird beim Speichern
  ins Dateiformat selbst geschrieben (ODF `meta.xml`/OOXML Core-Properties),
  bleibt nach Schließen/erneutem Öffnen erhalten.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SESSION_FILE = Path.home() / ".ogdoc" / "session.json"

PROP_DOCUMENT_ID = "ogdoc_document_id"
PROP_VERSION_NUMBER = "ogdoc_version_number"
PROP_CONTENT_TYPE = "ogdoc_content_type"


@dataclass(frozen=True)
class LinkedDocument:
    document_id: str
    version_number: int
    # Ursprünglicher Content-Type der Datei (z. B. DOCX vs. ODT) - beim
    # Speichern wiederverwendet, damit "In OG Doc speichern" nicht
    # stillschweigend in ein anderes Format konvertiert (echter, vor dem
    # Live-Test gefundener Bug, siehe PROGRESS.md).
    content_type: str


# --- Sitzung (Token) ---------------------------------------------------


def load_session() -> dict | None:
    if not SESSION_FILE.exists():
        return None
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_session(*, base_url: str, token: str, username: str) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(
        json.dumps({"base_url": base_url, "token": token, "username": username}),
        encoding="utf-8",
    )


def clear_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


# --- Dokument-Verknüpfung (UNO UserDefinedProperties) -----------------


def _set_property(props, name: str, value) -> None:
    info = props.getPropertySetInfo()
    if info.hasPropertyByName(name):
        props.setPropertyValue(name, value)
    else:
        # PropertyAttribute.REMOVABLE erlaubt späteres `removeProperty` beim
        # Lösen der Verknüpfung.
        from com.sun.star.beans import PropertyAttribute

        props.addProperty(name, PropertyAttribute.REMOVABLE, value)


def get_linked_document(doc) -> LinkedDocument | None:
    props = doc.getDocumentProperties().UserDefinedProperties
    info = props.getPropertySetInfo()
    if not info.hasPropertyByName(PROP_DOCUMENT_ID):
        return None
    document_id = props.getPropertyValue(PROP_DOCUMENT_ID)
    if not document_id:
        return None
    version_number = props.getPropertyValue(PROP_VERSION_NUMBER)
    content_type = (
        props.getPropertyValue(PROP_CONTENT_TYPE)
        if info.hasPropertyByName(PROP_CONTENT_TYPE)
        else "application/vnd.oasis.opendocument.text"
    )
    return LinkedDocument(document_id, int(version_number), content_type)


def set_linked_document(doc, document_id: str, version_number: int, content_type: str) -> None:
    props = doc.getDocumentProperties().UserDefinedProperties
    _set_property(props, PROP_DOCUMENT_ID, document_id)
    _set_property(props, PROP_VERSION_NUMBER, str(version_number))
    _set_property(props, PROP_CONTENT_TYPE, content_type)


def clear_linked_document(doc) -> None:
    props = doc.getDocumentProperties().UserDefinedProperties
    info = props.getPropertySetInfo()
    for name in (PROP_DOCUMENT_ID, PROP_VERSION_NUMBER, PROP_CONTENT_TYPE):
        if info.hasPropertyByName(name):
            props.removeProperty(name)
