from io import BytesIO
from typing import Literal

from dms_connector_sdk import (
    DmsTreeClient,
    LockConflictError,
    PathNotFoundError,
    TreeDocument,
    TreeFolder,
)
from wsgidav.dav_error import HTTP_FORBIDDEN, HTTP_LOCKED, DAVError
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider
from wsgidav.util import join_uri

from webdav_connector.license_client import LicenseStatusClient

_DEFAULT_ACTOR = "webdav-connector"
# WebDAV session identifier for the document-service lock (4.2) - a
# Basic-Auth session has no native session concept like a browser login,
# so this is a value that is stable per request but reusable across
# multiple requests from the same WebDAV client: the username itself is
# sufficient as a distinguishing feature, since document-service locking is
# per-document anyway (see docs/services/webdav-connector.md for the
# deliberate boundary - a lock held via a real WebDAV LOCK is NOT mirrored
# over the entire editing duration, only during each individual write
# operation).
_SESSION_PREFIX = "webdav:"


class _CapturingBuffer(BytesIO):
    """`wsgidav`'s real `do_PUT` handler calls `fileobj.close()` BEFORE
    calling `res.end_write()` (request_server.py) - a `getvalue()`
    afterwards would raise `ValueError: I/O operation on closed file`.
    Therefore the content is captured here upon closing, instead of only
    being read from the (by then already closed) buffer in `end_write()`."""

    def __init__(self, on_close) -> None:
        super().__init__()
        self._on_close = on_close

    def close(self) -> None:
        if not self.closed:
            self._on_close(self.getvalue())
        super().close()


def _actor(environ: dict) -> str:
    return environ.get("wsgidav.auth.user_name") or _DEFAULT_ACTOR


_BY_ID_PREFIX = "by-id/"


def _parse_by_id_path(path: str) -> str | None:
    """Office direct editing (post-roadmap feature): `/webdav/by-id/
    <document-id>.<ext>` instead of a folder path - `document-id` is a
    dot-free `uuid4()` (see document-service), so splitting on the LAST dot
    reliably strips off the purely cosmetic `.ext` suffix. Returns `None`
    if the path is not a `by-id/...` path (normal folder-path-based access
    remains completely unchanged)."""
    trimmed = path.strip("/")
    if not trimmed.startswith(_BY_ID_PREFIX):
        return None
    raw = trimmed[len(_BY_ID_PREFIX) :]
    return raw.rsplit(".", 1)[0] if "." in raw else raw


def _split_dest_path(dest_path: str) -> tuple[str, str]:
    """Splits a WebDAV destination path (from the `Destination` header,
    already reduced by wsgidav to the plain resource path) into
    (parent path, new name)."""
    trimmed = dest_path.strip("/")
    if "/" not in trimmed:
        return "", trimmed
    parent, name = trimmed.rsplit("/", 1)
    return parent, name


class _ByIdVirtualCollection(DAVCollection):
    """Office direct editing (post-roadmap feature): a purely synthetic
    collection for the `by-id/` namespace itself (not a real `TreeFolder`).
    Necessary because wsgidav's `do_PUT` handler resolves the target's
    parent path and checks `is_collection` against it BEFORE every write
    access (`request_server.py`, "PUT parent must be a collection") -
    without this class, `get_resource_inst("by-id")` (the parent path of
    `by-id/<document-id>.<ext>` as computed by wsgidav) would resolve to
    nothing (no real folder of that name), and every check-in of a new
    version via the Office direct editing path would fail with `409`. Not
    browsable (`get_member_names` deliberately returns nothing) - the only
    supported access pattern is `by-id/<document-id>.<ext>` directly, never
    a listing of the namespace."""

    def get_member_names(self) -> list[str]:
        return []


class DmsDavFolder(DAVCollection):
    def __init__(self, path: str, environ: dict, folder: TreeFolder) -> None:
        super().__init__(path, environ)
        self.folder = folder

    @property
    def _tree(self) -> DmsTreeClient:
        return self.provider.tree

    def get_display_name(self) -> str:
        return self.folder.name or "/"

    def get_member_names(self) -> list[str]:
        folders, documents = self._tree.list_children(self.folder.id)
        return [f.name for f in folders] + [d.title for d in documents]

    def get_member(self, name: str):
        folders, documents = self._tree.list_children(self.folder.id)
        folder_match = next((f for f in folders if f.name == name), None)
        if folder_match is not None:
            return DmsDavFolder(join_uri(self.path, name), self.environ, folder_match)
        document_match = next((d for d in documents if d.title == name), None)
        if document_match is not None:
            return DmsDavDocument(
                join_uri(self.path, name),
                self.environ,
                folder_id=self.folder.id,
                filename=name,
                document=document_match,
            )
        return None

    def create_collection(self, name: str):
        self.provider.check_license("write")
        created = self._tree.create_folder(
            parent_id=self.folder.id, name=name, created_by=_actor(self.environ)
        )
        return DmsDavFolder(join_uri(self.path, name), self.environ, created)

    def create_empty_resource(self, name: str):
        # Called for a PUT to a path that does not yet exist - the actual
        # creation in document-service only happens in
        # `DmsDavDocument.end_write()`, once the full content is available.
        return DmsDavDocument(
            join_uri(self.path, name),
            self.environ,
            folder_id=self.folder.id,
            filename=name,
            document=None,
        )

    def handle_delete(self) -> bool:
        # Native handling instead of wsgidav's generic "delete member by
        # member" (saves O(children) HTTP round trips and fits the tree
        # structure that already exists anyway).
        self.provider.check_license("write")
        folders, documents = self._tree.list_children(self.folder.id)
        for document in documents:
            self._tree.delete_document(document.id, deleted_by=_actor(self.environ))
        for folder in folders:
            DmsDavFolder(join_uri(self.path, folder.name), self.environ, folder).handle_delete()
        self._tree.delete_folder(self.folder.id)
        return True

    def handle_move(self, dest_path: str) -> bool:
        self.provider.check_license("write")
        parent_path, new_name = _split_dest_path(dest_path)
        try:
            target_parent = self._tree.resolve_path(parent_path)
        except PathNotFoundError as exc:
            raise DAVError(HTTP_FORBIDDEN) from exc
        if not isinstance(target_parent, TreeFolder):
            raise DAVError(HTTP_FORBIDDEN)
        self._tree.move_folder(self.folder.id, new_parent_id=target_parent.id, new_name=new_name)
        return True


class DmsDavDocument(DAVNonCollection):
    def __init__(
        self,
        path: str,
        environ: dict,
        *,
        folder_id: str,
        filename: str,
        document: TreeDocument | None,
    ) -> None:
        super().__init__(path, environ)
        self.folder_id = folder_id
        self.filename = filename
        self.document = document
        self._write_content: bytes | None = None
        self._write_content_type: str | None = None

    @property
    def _tree(self) -> DmsTreeClient:
        return self.provider.tree

    def get_display_name(self) -> str:
        return self.filename

    def get_content_length(self) -> int | None:
        return self.document.size_bytes if self.document else 0

    def get_content_type(self) -> str | None:
        return self.document.content_type if self.document else None

    def get_last_modified(self):
        return self.document.updated_at.timestamp() if self.document else None

    def get_etag(self):
        return self.document.checksum_sha256 if self.document else None

    def support_etag(self) -> bool:
        return True

    def support_ranges(self) -> bool:
        return False

    def get_content(self) -> BytesIO:
        assert self.document is not None
        return BytesIO(self._tree.read_document_content(self.document.id))

    def begin_write(self, *, content_type: str | None = None) -> BytesIO:
        def _capture(data: bytes) -> None:
            self._write_content = data

        self._write_content_type = content_type
        return _CapturingBuffer(_capture)

    def end_write(self, *, with_errors: bool) -> None:
        if with_errors or self._write_content is None:
            return
        self.provider.check_license("write")
        content = self._write_content
        actor = _actor(self.environ)

        # New creation (no `self.document` so far) needs no lock - nothing
        # exists yet that a concurrent edit could disturb. An update holds
        # document-service's real lock for the duration of the write
        # operation (4.2) - visible in the user UI while the WebDAV client
        # is currently writing.
        if self.document is None:
            self.document = self._tree.write_document(
                folder_id=self.folder_id,
                filename=self.filename,
                content=content,
                content_type=self._write_content_type,
                created_by=actor,
            )
            return

        try:
            self._tree.acquire_lock(
                self.document.id, locked_by=actor, session_id=f"{_SESSION_PREFIX}{actor}"
            )
        except LockConflictError as exc:
            raise DAVError(HTTP_LOCKED) from exc
        try:
            self.document = self._tree.write_document(
                folder_id=self.folder_id,
                filename=self.filename,
                content=content,
                content_type=self._write_content_type,
                created_by=actor,
                existing_document_id=self.document.id,
                expected_base_version_number=self.document.current_version_number,
            )
        finally:
            self._tree.release_lock(self.document.id, released_by=actor)

    def handle_delete(self) -> bool:
        assert self.document is not None
        self.provider.check_license("write")
        self._tree.delete_document(self.document.id, deleted_by=_actor(self.environ))
        return True

    def handle_move(self, dest_path: str) -> bool:
        assert self.document is not None
        self.provider.check_license("write")
        parent_path, new_name = _split_dest_path(dest_path)
        try:
            target_parent = self._tree.resolve_path(parent_path)
        except PathNotFoundError as exc:
            raise DAVError(HTTP_FORBIDDEN) from exc
        if not isinstance(target_parent, TreeFolder):
            raise DAVError(HTTP_FORBIDDEN)
        self._tree.move_document(
            self.document.id, new_folder_id=target_parent.id, new_title=new_name
        )
        return True


class DmsDavProvider(DAVProvider):
    """wsgidav `DAVProvider` that serves `folder-service`/`document-service`
    via `dms-connector-sdk`'s `DmsTreeClient` (3.3, P12-S1) - no filesystem
    of its own, every request translates live into HTTP calls against the
    existing DMS services."""

    def __init__(self, tree: DmsTreeClient, license_client: LicenseStatusClient) -> None:
        super().__init__()
        self.tree = tree
        self.license_client = license_client

    def check_license(self, action: Literal["read", "write"]) -> None:
        """Demo mode/lock behavior (concept 9.3, P9-S2 pattern) - connectors
        are explicitly named in 3.3 as a licensable component. Unlike
        `workflow_service`'s `Depends()` gate (real FastAPI routes there),
        this hooks in directly within the wsgidav callback methods, since
        actual WebDAV traffic does not run through FastAPI routes."""
        status = self.license_client.get_status()
        if status == "unlicensed":
            raise DAVError(
                HTTP_FORBIDDEN,
                "Lizenz erforderlich - Komponente 'webdav-connector' nicht lizenziert.",
            )
        if status == "demo" and action == "write":
            raise DAVError(HTTP_FORBIDDEN, "Demo-Modus aktiv - nur Lesezugriff verfügbar.")

    def get_resource_inst(self, path: str, environ: dict):
        self.check_license("read")
        if path.strip("/") == "by-id":
            # wsgidav's `do_PUT` handler resolves the target's parent path
            # and checks `is_collection` before every write access - for
            # `by-id/<document-id>.<ext>` that is exactly this bare
            # namespace path, see the `_ByIdVirtualCollection` docstring.
            return _ByIdVirtualCollection(path, environ)
        by_id = _parse_by_id_path(path)
        if by_id is not None:
            # Office direct editing (post-roadmap feature): direct ID
            # resolution instead of the otherwise usual folder-path-based
            # `resolve_path()` tree traversal (O(depth) HTTP calls) - the
            # start URL only knows the document ID, not a folder path. The
            # `.ext` suffix in the path only serves Office's own local file
            # type detection and is discarded here (the real content type
            # still comes from the document metadata).
            try:
                node = self.tree.get_document(by_id)
            except PathNotFoundError:
                return None
            return DmsDavDocument(
                path, environ, folder_id=node.folder_id, filename=node.title, document=node
            )
        try:
            node = self.tree.resolve_path(path)
        except PathNotFoundError:
            return None
        if isinstance(node, TreeFolder):
            return DmsDavFolder(path, environ, node)
        return DmsDavDocument(
            path, environ, folder_id=node.folder_id, filename=node.title, document=node
        )
