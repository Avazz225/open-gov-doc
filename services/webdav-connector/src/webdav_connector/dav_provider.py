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
# WebDAV-Sitzungs-Kennung fuer die document-service-Sperre (4.2) - eine
# Basic-Auth-Sitzung hat kein natives Session-Konzept wie ein Browser-Login,
# daher ein pro Request stabiler, aber ueber mehrere Requests desselben
# WebDAV-Clients wiederverwendbarer Wert: der Nutzername selbst reicht als
# Unterscheidungsmerkmal, da document-service Sperren ohnehin pro Dokument
# fuehrt (siehe docs/services/webdav-connector.md fuer die bewusste Grenze
# - eine ueber ein echtes WebDAV-LOCK gehaltene Sperre wird NICHT ueber die
# gesamte Bearbeitungsdauer gespiegelt, nur waehrend jeder einzelnen
# Schreiboperation).
_SESSION_PREFIX = "webdav:"


class _CapturingBuffer(BytesIO):
    """`wsgidav`s echter `do_PUT`-Handler ruft `fileobj.close()` auf, BEVOR er
    `res.end_write()` aufruft (request_server.py) - ein `getvalue()` danach
    wuerde `ValueError: I/O operation on closed file` werfen. Deshalb wird der
    Inhalt hier beim Schliessen abgegriffen, statt ihn erst in `end_write()`
    aus dem (zu dem Zeitpunkt schon geschlossenen) Puffer zu lesen."""

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
    """Office-Direktbearbeitung (Post-Roadmap-Feature): `/webdav/by-id/
    <document-id>.<ext>` statt eines Ordnerpfads - `document-id` ist ein
    punktloses `uuid4()` (siehe document-service), das Splitten auf den
    LETZTEN Punkt trennt also zuverlässig die rein kosmetische `.ext`-Endung
    ab. Liefert `None`, wenn der Pfad kein `by-id/...`-Pfad ist (normaler
    ordnerpfadbasierter Zugriff bleibt komplett unverändert)."""
    trimmed = path.strip("/")
    if not trimmed.startswith(_BY_ID_PREFIX):
        return None
    raw = trimmed[len(_BY_ID_PREFIX) :]
    return raw.rsplit(".", 1)[0] if "." in raw else raw


def _split_dest_path(dest_path: str) -> tuple[str, str]:
    """Zerlegt einen WebDAV-Zielpfad (aus dem `Destination`-Header, von
    wsgidav bereits auf den reinen Ressourcenpfad reduziert) in
    (Elternpfad, neuer Name)."""
    trimmed = dest_path.strip("/")
    if "/" not in trimmed:
        return "", trimmed
    parent, name = trimmed.rsplit("/", 1)
    return parent, name


class _ByIdVirtualCollection(DAVCollection):
    """Office-Direktbearbeitung (Post-Roadmap-Feature): rein synthetische
    Kollektion für den `by-id/`-Namensraum selbst (kein echter `TreeFolder`).
    Nötig, weil wsgidavs `do_PUT`-Handler VOR jedem Schreibzugriff den
    Elternpfad des Ziels auflöst und `is_collection` darauf prüft
    (`request_server.py`, "PUT parent must be a collection") - ohne diese
    Klasse würde `get_resource_inst("by-id")` (der von wsgidav berechnete
    Elternpfad von `by-id/<document-id>.<ext>`) ins Leere laufen (kein realer
    Ordner dieses Namens), und jedes Einchecken einer neuen Version über den
    Office-Direktbearbeitungs-Pfad würde mit `409` fehlschlagen. Nicht
    durchsuchbar (`get_member_names` liefert bewusst nichts) - die einzige
    unterstützte Zugriffsart ist `by-id/<document-id>.<ext>` direkt, nie eine
    Auflistung des Namensraums."""

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
        # Wird fuer PUT auf einen noch nicht existierenden Pfad aufgerufen -
        # das eigentliche Anlegen in document-service passiert erst in
        # `DmsDavDocument.end_write()`, sobald der volle Inhalt vorliegt.
        return DmsDavDocument(
            join_uri(self.path, name),
            self.environ,
            folder_id=self.folder.id,
            filename=name,
            document=None,
        )

    def handle_delete(self) -> bool:
        # Native Behandlung statt wsgidavs generischem "Mitglied fuer
        # Mitglied loeschen" (spart O(Kinder) HTTP-Rundreisen und passt zur
        # ohnehin vorhandenen Baumstruktur).
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

        # Neuanlage (kein `self.document` bislang) braucht keine Sperre -
        # es existiert noch nichts, das eine parallele Bearbeitung stoeren
        # koennte. Ein Update haelt document-services echte Sperre fuer die
        # Dauer des Schreibvorgangs (4.2) - sichtbar in der User-UI, waehrend
        # der WebDAV-Client gerade schreibt.
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
    """wsgidav-`DAVProvider`, der `folder-service`/`document-service` über
    `dms-connector-sdk`s `DmsTreeClient` bedient (3.3, P12-S1) - kein eigenes
    Dateisystem, jede Anfrage übersetzt sich live in HTTP-Aufrufe gegen die
    bestehenden DMS-Services."""

    def __init__(self, tree: DmsTreeClient, license_client: LicenseStatusClient) -> None:
        super().__init__()
        self.tree = tree
        self.license_client = license_client

    def check_license(self, action: Literal["read", "write"]) -> None:
        """Demo-Modus/Sperrverhalten (Konzept 9.3, P9-S2-Muster) - Connectoren
        sind laut 3.3 wörtlich als lizenzierbare Komponente genannt. Anders
        als `workflow_service`s `Depends()`-Gate (dort echte FastAPI-Routen)
        greift dies hier direkt in den wsgidav-Callback-Methoden, da der
        eigentliche WebDAV-Verkehr nicht über FastAPI-Routen läuft."""
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
            # wsgidavs `do_PUT`-Handler löst vor jedem Schreibzugriff den
            # Elternpfad des Ziels auf und prüft `is_collection` - für
            # `by-id/<document-id>.<ext>` ist das genau dieser bare
            # Namensraum-Pfad, siehe `_ByIdVirtualCollection`-Docstring.
            return _ByIdVirtualCollection(path, environ)
        by_id = _parse_by_id_path(path)
        if by_id is not None:
            # Office-Direktbearbeitung (Post-Roadmap-Feature): direkte
            # ID-Auflösung statt des ansonsten üblichen ordnerpfadbasierten
            # `resolve_path()`-Baumdurchlaufs (O(Tiefe) HTTP-Aufrufe) - die
            # Start-URL kennt nur die Dokument-ID, keinen Ordnerpfad. Die
            # `.ext`-Endung im Pfad dient nur der lokalen Dateityp-Erkennung
            # von Office selbst und wird hier verworfen (der echte
            # Content-Type kommt weiterhin aus den Dokumentmetadaten).
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
