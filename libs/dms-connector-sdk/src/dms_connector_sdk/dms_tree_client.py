from dataclasses import dataclass
from datetime import datetime

import httpx


class PathNotFoundError(Exception):
    """Ein Pfadsegment (Ordner oder Dokument) existiert nicht - sowohl beim
    Auflösen eines Pfads als auch bei Move/Create gegen ein unbekanntes Ziel."""


class LockConflictError(Exception):
    """Das Dokument ist bereits von einer anderen Sitzung gesperrt (409, siehe
    document-service `POST /documents/{id}/lock`)."""


class LockNotHeldError(Exception):
    """Freigabeversuch einer Sperre, die eine andere Sitzung hält (403)."""


@dataclass(frozen=True)
class TreeFolder:
    id: str
    name: str
    parent_id: str | None


@dataclass(frozen=True)
class TreeDocument:
    id: str
    title: str
    folder_id: str | None
    size_bytes: int
    content_type: str | None
    checksum_sha256: str | None
    updated_at: datetime
    current_version_number: int


@dataclass(frozen=True)
class TreeLock:
    document_id: str
    locked_by: str
    session_id: str
    expires_at: datetime


def _to_tree_document(body: dict, version: dict | None = None) -> TreeDocument:
    """`DocumentOut` (das `body`-Argument) traegt selbst keine Datei-Metadaten
    (Groesse/Content-Type/Pruefsumme) - die leben ausschliesslich auf
    `DocumentVersionOut` der jeweils aktuellen Version. `version` ist daher
    ein separat abgerufener `DocumentVersionOut`-Body (siehe
    `DmsTreeClient._fetch_current_version`); ohne ihn (z. B. wenn der
    Aufrufer die Werte bewusst nicht braucht) bleiben die Felder auf
    neutralen Defaults - WICHTIG: nicht `checksum_sha256=""`, das wsgidav
    als ETag ablehnt (`checked_etag` erlaubt `None`, aber keinen leeren
    String), siehe `DmsDavDocument.get_etag()`."""
    version = version or {}
    return TreeDocument(
        id=body["id"],
        title=body["title"],
        folder_id=body["folder_id"],
        size_bytes=version.get("size_bytes", 0),
        content_type=version.get("content_type"),
        checksum_sha256=version.get("checksum_sha256") or None,
        updated_at=datetime.fromisoformat(body["updated_at"]),
        current_version_number=body["current_version_number"],
    )


def _to_tree_folder(body: dict) -> TreeFolder:
    return TreeFolder(id=body["id"], name=body["name"], parent_id=body["parent_id"])


class DmsTreeClient:
    """Wiederverwendbare DMS-Baum-Anbindung für Connector-Services (3.3) -
    kennt weder WebDAV noch CMIS, nur die Übersetzung "Pfad/Ordner/Dokument"
    auf die bestehenden `folder-service`/`document-service`-HTTP-APIs.

    Bewusst **synchron** (`httpx.Client`, nicht `AsyncClient`): der erste
    Connector (`webdav-connector`, P12-S1) baut auf `wsgidav` auf, dessen
    `DAVProvider`-Schnittstelle selbst synchron ist (WSGI) - eine
    async-Variante hier hätte im Provider eine async/sync-Brücke
    (`asgiref.async_to_sync`) gebraucht, ein bekannt fragiles Muster über
    mehrere verschachtelte Thread-/Loop-Grenzen hinweg. Ein künftiger
    FastAPI-basierter Connector (z. B. CMIS, P12-S4) kann diese Lib weiterhin
    gefahrlos nutzen: FastAPI führt normale `def`-Endpunkte (kein `async def`)
    bereits automatisch in seinem eigenen Threadpool aus."""

    def __init__(
        self,
        *,
        document_service_base_url: str,
        folder_service_base_url: str,
        root_folder_id: str = "root",
    ) -> None:
        self._documents = httpx.Client(base_url=document_service_base_url, timeout=30.0)
        self._folders = httpx.Client(base_url=folder_service_base_url, timeout=30.0)
        self.root_folder_id = root_folder_id

    def close(self) -> None:
        self._documents.close()
        self._folders.close()

    def _fetch_current_version(self, document_id: str, version_number: int) -> dict:
        response = self._documents.get(f"/documents/{document_id}/versions/{version_number}")
        response.raise_for_status()
        return response.json()

    def _to_tree_document_enriched(self, body: dict) -> TreeDocument:
        version = self._fetch_current_version(body["id"], body["current_version_number"])
        return _to_tree_document(body, version)

    def list_children(self, folder_id: str) -> tuple[list[TreeFolder], list[TreeDocument]]:
        folders_response = self._folders.get(f"/folders/{folder_id}/children")
        if folders_response.status_code == 404:
            raise PathNotFoundError(folder_id)
        folders_response.raise_for_status()
        documents_response = self._documents.get("/documents", params={"folder_id": folder_id})
        documents_response.raise_for_status()
        folders = [_to_tree_folder(f) for f in folders_response.json() if f["deleted_at"] is None]
        # Ein zusaetzlicher HTTP-Aufruf je Dokument (Versions-Metadaten leben
        # nicht auf `DocumentOut`, siehe `_to_tree_document`) - fuer eine
        # Referenzimplementierung bewusst in Kauf genommen: WebDAV-Clients
        # (Windows-Explorer/Finder) verlassen sich auf korrekte
        # Content-Length/ETag-Werte in der Verzeichnisauflistung, ein falscher
        # Defaultwert waere die schlechtere Alternative.
        documents = [
            self._to_tree_document_enriched(d)
            for d in documents_response.json()
            if d["deleted_at"] is None
        ]
        return folders, documents

    def resolve_path(self, path: str) -> TreeFolder | TreeDocument:
        """Löst einen Slash-Pfad segmentweise ab `root_folder_id` auf - ein
        Connector kennt keine eigene Kopie der Ordnerstruktur (3.1), jede
        Auflösung fragt live nach. O(Tiefe) HTTP-Aufrufe je Pfad, für eine
        Referenzimplementierung bewusst einfach gehalten statt eines eigenen
        Caches mit Invalidierungsproblemen."""
        segments = [s for s in path.strip("/").split("/") if s]
        if not segments:
            return TreeFolder(id=self.root_folder_id, name="", parent_id=None)

        current_folder_id = self.root_folder_id
        for index, segment in enumerate(segments):
            is_last = index == len(segments) - 1
            folders, documents = self.list_children(current_folder_id)
            folder_match = next((f for f in folders if f.name == segment), None)
            if folder_match is not None:
                if is_last:
                    return folder_match
                current_folder_id = folder_match.id
                continue
            if is_last:
                document_match = next((d for d in documents if d.title == segment), None)
                if document_match is not None:
                    return document_match
            raise PathNotFoundError(path)
        raise PathNotFoundError(path)  # unerreichbar, beruhigt den Type-Checker

    def create_folder(self, *, parent_id: str, name: str, created_by: str) -> TreeFolder:
        response = self._folders.post(
            "/folders", json={"name": name, "parent_id": parent_id, "created_by": created_by}
        )
        if response.status_code == 404:
            raise PathNotFoundError(parent_id)
        response.raise_for_status()
        return _to_tree_folder(response.json())

    def get_folder(self, folder_id: str) -> TreeFolder:
        response = self._folders.get(f"/folders/{folder_id}")
        if response.status_code == 404:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()
        return _to_tree_folder(response.json())

    def get_document(self, document_id: str) -> TreeDocument:
        response = self._documents.get(f"/documents/{document_id}")
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json())

    def read_document_content(self, document_id: str) -> bytes:
        response = self._documents.get(f"/documents/{document_id}/content")
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()
        return response.content

    def write_document(
        self,
        *,
        folder_id: str,
        filename: str,
        content: bytes,
        content_type: str | None,
        created_by: str,
        existing_document_id: str | None = None,
        expected_base_version_number: int | None = None,
    ) -> TreeDocument:
        """PUT-Semantik (WebDAV/CMIS gleichermaßen): existiert am Zielpfad
        bereits ein Dokument, wird eine neue Version eingecheckt statt ein
        zweites Dokument mit demselben Namen anzulegen."""
        media_type = content_type or "application/octet-stream"
        if existing_document_id is not None:
            response = self._documents.post(
                f"/documents/{existing_document_id}/versions",
                data={
                    "expected_base_version_number": str(expected_base_version_number),
                    "created_by": created_by,
                },
                files={"file": (filename, content, media_type)},
            )
            if response.status_code == 404:
                raise PathNotFoundError(existing_document_id)
            response.raise_for_status()
            document_response = self._documents.get(f"/documents/{existing_document_id}")
            document_response.raise_for_status()
            return self._to_tree_document_enriched(document_response.json())

        response = self._documents.post(
            "/documents",
            data={"title": filename, "created_by": created_by, "folder_id": folder_id},
            files={"file": (filename, content, media_type)},
        )
        if response.status_code == 400:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json())

    def delete_document(self, document_id: str, *, deleted_by: str) -> None:
        response = self._documents.delete(
            f"/documents/{document_id}", params={"deleted_by": deleted_by}
        )
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()

    def delete_folder(self, folder_id: str) -> None:
        response = self._folders.delete(f"/folders/{folder_id}")
        if response.status_code == 404:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()

    def move_document(
        self, document_id: str, *, new_folder_id: str | None = None, new_title: str | None = None
    ) -> TreeDocument:
        payload: dict = {}
        if new_folder_id is not None:
            payload["folder_id"] = new_folder_id
        if new_title is not None:
            payload["title"] = new_title
        response = self._documents.patch(f"/documents/{document_id}", json=payload)
        if response.status_code in (400, 404):
            raise PathNotFoundError(new_folder_id or document_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json())

    def move_folder(
        self, folder_id: str, *, new_parent_id: str | None = None, new_name: str | None = None
    ) -> TreeFolder:
        payload: dict = {}
        if new_parent_id is not None:
            payload["parent_id"] = new_parent_id
        if new_name is not None:
            payload["name"] = new_name
        response = self._folders.patch(f"/folders/{folder_id}", json=payload)
        if response.status_code in (400, 404):
            raise PathNotFoundError(new_parent_id or folder_id)
        response.raise_for_status()
        return _to_tree_folder(response.json())

    def acquire_lock(
        self,
        document_id: str,
        *,
        locked_by: str,
        session_id: str,
        timeout_seconds: float | None = None,
    ) -> TreeLock:
        payload: dict = {"locked_by": locked_by, "session_id": session_id}
        if timeout_seconds is not None:
            payload["timeout_seconds"] = timeout_seconds
        response = self._documents.post(f"/documents/{document_id}/lock", json=payload)
        if response.status_code == 409:
            raise LockConflictError(document_id)
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()
        body = response.json()
        return TreeLock(
            document_id=body["document_id"],
            locked_by=body["locked_by"],
            session_id=body["session_id"],
            expires_at=datetime.fromisoformat(body["expires_at"]),
        )

    def release_lock(self, document_id: str, *, released_by: str) -> None:
        response = self._documents.request(
            "DELETE", f"/documents/{document_id}/lock", json={"released_by": released_by}
        )
        if response.status_code == 403:
            raise LockNotHeldError(document_id)
        if response.status_code == 404:
            return  # bereits entsperrt/unbekannt - UNLOCK ist idempotent
        response.raise_for_status()

    def get_lock(self, document_id: str) -> TreeLock | None:
        # Liefert immer 200 mit `null`-Body, wenn keine Sperre existiert -
        # prüft nicht einmal, ob `document_id` überhaupt existiert (siehe
        # document-service main.py `get_lock()`), daher kein 404-Zweig hier.
        response = self._documents.get(f"/documents/{document_id}/lock")
        response.raise_for_status()
        body = response.json()
        if body is None:
            return None
        return TreeLock(
            document_id=body["document_id"],
            locked_by=body["locked_by"],
            session_id=body["session_id"],
            expires_at=datetime.fromisoformat(body["expires_at"]),
        )
