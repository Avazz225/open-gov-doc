from dataclasses import dataclass
from datetime import datetime

import httpx


class PathNotFoundError(Exception):
    """A path segment (folder or document) does not exist - both when
    resolving a path and on move/create against an unknown target."""


class LockConflictError(Exception):
    """The document is already locked by another session (409, see
    document-service `POST /documents/{id}/lock`)."""


class LockNotHeldError(Exception):
    """Attempt to release a lock held by another session (403)."""


@dataclass(frozen=True)
class TreeFolder:
    id: str
    name: str
    parent_id: str | None
    # `str | None`/`datetime | None` instead of mandatory fields: the free
    # local root short-circuit construction in `resolve_path()` (below)
    # doesn't know either value, without risking an extra HTTP call in the
    # hot path (traversed on every WebDAV request) - `None` is CMIS' own
    # "value not set" state (5.2.7), not a made-up value.
    created_by: str | None
    created_at: datetime | None


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
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class TreeLock:
    document_id: str
    locked_by: str
    session_id: str
    expires_at: datetime


def _to_tree_document(body: dict, version: dict | None = None) -> TreeDocument:
    """`DocumentOut` (the `body` argument) itself carries no file metadata
    (size/content type/checksum) - those live exclusively on
    `DocumentVersionOut` of the respective current version. `version` is
    therefore a separately fetched `DocumentVersionOut` body (see
    `DmsTreeClient._fetch_current_version`); without it (e.g. when the
    caller deliberately doesn't need the values) the fields stay at
    neutral defaults - IMPORTANT: not `checksum_sha256=""`, which wsgidav
    rejects as an ETag (`checked_etag` allows `None` but not an empty
    string), see `DmsDavDocument.get_etag()`."""
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
        created_by=body["created_by"],
        created_at=datetime.fromisoformat(body["created_at"]),
    )


def _to_tree_folder(body: dict) -> TreeFolder:
    return TreeFolder(
        id=body["id"],
        name=body["name"],
        parent_id=body["parent_id"],
        created_by=body["created_by"],
        created_at=datetime.fromisoformat(body["created_at"]),
    )


class DmsTreeClient:
    """Reusable DMS tree integration for connector services (3.3) - knows
    neither WebDAV nor CMIS, only the translation of "path/folder/document"
    onto the existing `folder-service`/`document-service` HTTP APIs.

    Deliberately **synchronous** (`httpx.Client`, not `AsyncClient`): the
    first connector (`webdav-connector`, P12-S1) is built on `wsgidav`,
    whose `DAVProvider` interface is itself synchronous (WSGI) - an async
    variant here would have needed an async/sync bridge
    (`asgiref.async_to_sync`) in the provider, a known-fragile pattern across
    multiple nested thread/loop boundaries. A future FastAPI-based connector
    (e.g. CMIS, P12-S4) can still safely use this lib: FastAPI already runs
    regular `def` endpoints (not `async def`) automatically in its own
    thread pool."""

    def __init__(
        self,
        *,
        document_service_base_url: str,
        folder_service_base_url: str,
        root_folder_id: str = "root",
        default_principal: str = "",
    ) -> None:
        """`default_principal` (Post-Roadmap Phase 38 Session 4, ADR 0149):
        folder-service's/document-service's core endpoints now require a
        valid `X-DMS-Principal`. Set here as a per-client default for
        callers with no real per-request actor (e.g. migration-service,
        a background system identity); callers that DO have one (e.g.
        webdav-connector/cmis-connector, which already resolve the
        authenticated user per request for `created_by`/`deleted_by`
        elsewhere) pass `x_dms_principal=` on the individual calls below
        instead, which httpx overrides this default with."""
        default_headers = {"X-DMS-Principal": default_principal} if default_principal else {}
        self._documents = httpx.Client(
            base_url=document_service_base_url, timeout=30.0, headers=default_headers
        )
        self._folders = httpx.Client(
            base_url=folder_service_base_url, timeout=30.0, headers=default_headers
        )
        self.root_folder_id = root_folder_id

    def close(self) -> None:
        self._documents.close()
        self._folders.close()

    @staticmethod
    def _principal_headers(x_dms_principal: str) -> dict | None:
        return {"X-DMS-Principal": x_dms_principal} if x_dms_principal else None

    def _fetch_current_version(
        self, document_id: str, version_number: int, *, x_dms_principal: str = ""
    ) -> dict:
        response = self._documents.get(
            f"/documents/{document_id}/versions/{version_number}",
            headers=self._principal_headers(x_dms_principal),
        )
        response.raise_for_status()
        return response.json()

    def _to_tree_document_enriched(self, body: dict, *, x_dms_principal: str = "") -> TreeDocument:
        version = self._fetch_current_version(
            body["id"], body["current_version_number"], x_dms_principal=x_dms_principal
        )
        return _to_tree_document(body, version)

    def _fetch_current_versions_batch(
        self, document_ids: list[str], *, x_dms_principal: str = ""
    ) -> dict[str, dict]:
        """Phase 50 Session 4: batch counterpart to `_fetch_current_version`
        above - one round trip for the whole list instead of one per
        document, see `list_children` below (the actual N+1 this fixes)."""
        if not document_ids:
            return {}
        response = self._documents.post(
            "/documents/versions/current/batch",
            json={"document_ids": document_ids},
            headers=self._principal_headers(x_dms_principal),
        )
        response.raise_for_status()
        return response.json()["versions"]

    def list_children(
        self, folder_id: str, *, x_dms_principal: str = ""
    ) -> tuple[list[TreeFolder], list[TreeDocument]]:
        folders_response = self._folders.get(
            f"/folders/{folder_id}/children", headers=self._principal_headers(x_dms_principal)
        )
        if folders_response.status_code == 404:
            raise PathNotFoundError(folder_id)
        folders_response.raise_for_status()
        documents_response = self._documents.get(
            "/documents",
            params={"folder_id": folder_id},
            headers=self._principal_headers(x_dms_principal),
        )
        documents_response.raise_for_status()
        folders = [_to_tree_folder(f) for f in folders_response.json() if f["deleted_at"] is None]
        # Phase 50 Session 4: previously one extra HTTP call PER document
        # (version metadata doesn't live on `DocumentOut`, see
        # `_to_tree_document`) - a genuine N+1 that degraded root `PROPFIND`
        # with document volume (see docs/services/webdav-connector.md). Now
        # a single batched call for the whole folder - WebDAV clients
        # (Windows Explorer/Finder) still get correct Content-Length/ETag
        # values in the directory listing, just without the per-document
        # round trip. A document whose version the batch endpoint omitted
        # (deleted between the two calls above, or individually access-
        # restricted) falls back to `_to_tree_document`'s existing
        # `version=None` neutral defaults, same as before this session.
        live_documents = [d for d in documents_response.json() if d["deleted_at"] is None]
        versions = self._fetch_current_versions_batch(
            [d["id"] for d in live_documents], x_dms_principal=x_dms_principal
        )
        documents = [_to_tree_document(d, versions.get(d["id"])) for d in live_documents]
        return folders, documents

    def resolve_path(self, path: str, *, x_dms_principal: str = "") -> TreeFolder | TreeDocument:
        """Resolves a slash path segment by segment starting from
        `root_folder_id` - a connector keeps no local copy of the folder
        structure (3.1), every resolution queries live. O(depth) HTTP calls
        per path, deliberately kept simple for a reference implementation
        instead of a custom cache with invalidation problems."""
        segments = [s for s in path.strip("/").split("/") if s]
        if not segments:
            # Deliberately constructed locally, no `get_folder()` call: this
            # branch is traversed on EVERY WebDAV request to the root
            # (`get_resource_inst("/")`) - an extra roundtrip here turned out
            # in practice to cause a hang under wsgidav's WSGI-to-ASGI bridge
            # (reproducible `ReadTimeout` on PROPFIND against `/webdav/`,
            # see docs/services/cmis-connector.md). `created_by`/
            # `created_at` stay `None` (CMIS' "value not set", 5.2.7)
            # instead of made-up values or an expensive call in the hot path.
            return TreeFolder(
                id=self.root_folder_id, name="", parent_id=None, created_by=None, created_at=None
            )

        current_folder_id = self.root_folder_id
        for index, segment in enumerate(segments):
            is_last = index == len(segments) - 1
            folders, documents = self.list_children(
                current_folder_id, x_dms_principal=x_dms_principal
            )
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
        raise PathNotFoundError(path)  # unreachable, satisfies the type checker

    def create_folder(
        self, *, parent_id: str, name: str, created_by: str, x_dms_principal: str = ""
    ) -> TreeFolder:
        response = self._folders.post(
            "/folders",
            json={"name": name, "parent_id": parent_id, "created_by": created_by},
            headers=self._principal_headers(x_dms_principal),
        )
        if response.status_code == 404:
            raise PathNotFoundError(parent_id)
        response.raise_for_status()
        return _to_tree_folder(response.json())

    def get_folder(self, folder_id: str, *, x_dms_principal: str = "") -> TreeFolder:
        response = self._folders.get(
            f"/folders/{folder_id}", headers=self._principal_headers(x_dms_principal)
        )
        if response.status_code == 404:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()
        return _to_tree_folder(response.json())

    def get_document(self, document_id: str, *, x_dms_principal: str = "") -> TreeDocument:
        response = self._documents.get(
            f"/documents/{document_id}", headers=self._principal_headers(x_dms_principal)
        )
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json(), x_dms_principal=x_dms_principal)

    def read_document_content(self, document_id: str, *, x_dms_principal: str = "") -> bytes:
        response = self._documents.get(
            f"/documents/{document_id}/content", headers=self._principal_headers(x_dms_principal)
        )
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
        comment: str | None = None,
        x_dms_principal: str = "",
    ) -> TreeDocument:
        """PUT semantics (WebDAV/CMIS alike): if a document already exists at
        the target path, a new version is checked in instead of creating a
        second document with the same name. `comment` (only relevant when
        checking in a new version, `POST /documents` itself has no comment
        field) - since P12-S4, groundwork for CMIS'
        `checkinComment` (5.4.4.3.28)."""
        media_type = content_type or "application/octet-stream"
        headers = self._principal_headers(x_dms_principal)
        if existing_document_id is not None:
            data = {
                "expected_base_version_number": str(expected_base_version_number),
                "created_by": created_by,
            }
            if comment is not None:
                data["comment"] = comment
            response = self._documents.post(
                f"/documents/{existing_document_id}/versions",
                data=data,
                files={"file": (filename, content, media_type)},
                headers=headers,
            )
            if response.status_code == 404:
                raise PathNotFoundError(existing_document_id)
            response.raise_for_status()
            document_response = self._documents.get(
                f"/documents/{existing_document_id}", headers=headers
            )
            document_response.raise_for_status()
            return self._to_tree_document_enriched(
                document_response.json(), x_dms_principal=x_dms_principal
            )

        response = self._documents.post(
            "/documents",
            data={"title": filename, "created_by": created_by, "folder_id": folder_id},
            files={"file": (filename, content, media_type)},
            headers=headers,
        )
        if response.status_code == 400:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json(), x_dms_principal=x_dms_principal)

    def delete_document(
        self, document_id: str, *, deleted_by: str, x_dms_principal: str = ""
    ) -> None:
        response = self._documents.delete(
            f"/documents/{document_id}",
            params={"deleted_by": deleted_by},
            headers=self._principal_headers(x_dms_principal),
        )
        if response.status_code == 404:
            raise PathNotFoundError(document_id)
        response.raise_for_status()

    def delete_folder(self, folder_id: str, *, x_dms_principal: str = "") -> None:
        response = self._folders.delete(
            f"/folders/{folder_id}", headers=self._principal_headers(x_dms_principal)
        )
        if response.status_code == 404:
            raise PathNotFoundError(folder_id)
        response.raise_for_status()

    def move_document(
        self,
        document_id: str,
        *,
        new_folder_id: str | None = None,
        new_title: str | None = None,
        x_dms_principal: str = "",
    ) -> TreeDocument:
        payload: dict = {}
        if new_folder_id is not None:
            payload["folder_id"] = new_folder_id
        if new_title is not None:
            payload["title"] = new_title
        response = self._documents.patch(
            f"/documents/{document_id}",
            json=payload,
            headers=self._principal_headers(x_dms_principal),
        )
        if response.status_code in (400, 404):
            raise PathNotFoundError(new_folder_id or document_id)
        response.raise_for_status()
        return self._to_tree_document_enriched(response.json(), x_dms_principal=x_dms_principal)

    def move_folder(
        self,
        folder_id: str,
        *,
        new_parent_id: str | None = None,
        new_name: str | None = None,
        x_dms_principal: str = "",
    ) -> TreeFolder:
        payload: dict = {}
        if new_parent_id is not None:
            payload["parent_id"] = new_parent_id
        if new_name is not None:
            payload["name"] = new_name
        response = self._folders.patch(
            f"/folders/{folder_id}",
            json=payload,
            headers=self._principal_headers(x_dms_principal),
        )
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
        x_dms_principal: str = "",
    ) -> TreeLock:
        payload: dict = {"locked_by": locked_by, "session_id": session_id}
        if timeout_seconds is not None:
            payload["timeout_seconds"] = timeout_seconds
        response = self._documents.post(
            f"/documents/{document_id}/lock",
            json=payload,
            headers=self._principal_headers(x_dms_principal),
        )
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

    def release_lock(
        self, document_id: str, *, released_by: str, x_dms_principal: str = ""
    ) -> None:
        response = self._documents.request(
            "DELETE",
            f"/documents/{document_id}/lock",
            json={"released_by": released_by},
            headers=self._principal_headers(x_dms_principal),
        )
        if response.status_code == 403:
            raise LockNotHeldError(document_id)
        if response.status_code == 404:
            return  # already unlocked/unknown - UNLOCK is idempotent
        response.raise_for_status()

    def get_lock(self, document_id: str, *, x_dms_principal: str = "") -> TreeLock | None:
        # Always returns 200 with a `null` body when no lock exists - doesn't
        # even check whether `document_id` exists at all (see document-service
        # main.py `get_lock()`), hence no 404 branch here.
        response = self._documents.get(
            f"/documents/{document_id}/lock", headers=self._principal_headers(x_dms_principal)
        )
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
