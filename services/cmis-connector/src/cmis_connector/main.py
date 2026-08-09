"""CMIS 1.1 Browser Binding (Kapitel 5 der OASIS-CMIS-1.1-Spezifikation) -
von Hand implementiert, da keine gepflegte Python-CMIS-*Server*-Bibliothek
existiert (siehe ADR 0036). Reicht bewusst nur einen Teilumfang ab (~14
Endpunkte: repositoryInfo/children/object/content lesend, createDocument/
createFolder/update/move/delete/deleteTree/setContent/checkOut/
cancelCheckOut/checkIn schreibend) - vergleichbar im Umfang mit dem
WebDAV-Kernmethodenset aus P12-S1, aber ohne eine `wsgidav`-Entsprechung zum
Anlehnen. Details/Scope-Begründung siehe docs/services/cmis-connector.md."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_connector_sdk import (
    ConnectorDescriptor,
    DmsTreeClient,
    LockConflictError,
    PathNotFoundError,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from cmis_connector.auth import BasicAuthClient, parse_basic_auth
from cmis_connector.errors import CmisError, register_exception_handlers
from cmis_connector.license_client import LicenseStatusClient
from cmis_connector.properties import CMIS_VERSION_SUPPORTED, document_properties, folder_properties
from cmis_connector.resolve import ResolvedObject, compute_folder_path, resolve_object
from cmis_connector.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_DESCRIPTOR = ConnectorDescriptor(
    protocol="cmis",
    capabilities=frozenset({"read", "write", "metadata", "locking", "versioning"}),
)

# Bewusst auf Modulebene konstruiert (gleiches Muster wie webdav_connector.main,
# P12-S1): alle drei Clients sind synchron, kein async-Connect-Schritt nötig.
_tree = DmsTreeClient(
    document_service_base_url=settings.document_service_base_url,
    folder_service_base_url=settings.folder_service_base_url,
    root_folder_id=settings.cmis_root_folder_id,
)
_license_client = LicenseStatusClient(
    settings.registry_service_base_url or "",
    settings.service_name,
    settings.license_status_cache_ttl_seconds,
)
_auth_client = BasicAuthClient(settings.auth_service_base_url)


def _check_license(action: str) -> None:
    """Demo-Modus/Sperrverhalten (9.3, P9-S2-Muster) - Connectoren sind laut
    3.3 wörtlich als lizenzierbare Komponente genannt, identisches Verhalten
    wie `webdav_connector.dav_provider.DmsDavProvider.check_license`."""
    status_ = _license_client.get_status()
    if status_ == "unlicensed":
        raise CmisError(
            "permissionDenied",
            "Lizenz erforderlich - Komponente 'cmis-connector' nicht lizenziert.",
        )
    if status_ == "demo" and action == "write":
        raise CmisError("permissionDenied", "Demo-Modus aktiv - nur Lesezugriff verfügbar.")


def require_actor(credentials: tuple[str, str] = Depends(parse_basic_auth)) -> str:
    username, password = credentials
    if not _auth_client.verify(username, password):
        raise HTTPException(status_code=403, detail="Ungültige Anmeldedaten")
    return username


def _require_repository(repository_id: str) -> None:
    if repository_id != settings.cmis_repository_id:
        raise CmisError("objectNotFound", f"Unbekanntes Repository {repository_id!r}")


def _repository_info() -> dict:
    root_url = f"{settings.self_address}/browser/{settings.cmis_repository_id}/root"
    return {
        "repositoryId": settings.cmis_repository_id,
        "repositoryName": "DMS",
        "repositoryDescription": "DMS CMIS-Referenz-Connector (Konzept 3.3, P12-S4)",
        "vendorName": "DMS-Projekt",
        "productName": "cmis-connector",
        "productVersion": "0.1.0",
        "rootFolderId": settings.cmis_root_folder_id,
        "rootFolderUrl": root_url,
        "repositoryUrl": f"{settings.self_address}/browser/{settings.cmis_repository_id}",
        "cmisVersionSupported": CMIS_VERSION_SUPPORTED,
        "changesIncomplete": True,
        "capabilities": {
            "capabilityContentStreamUpdatability": "anytime",
            "capabilityChanges": "none",
            "capabilityRenditions": "none",
            "capabilityGetDescendants": False,
            "capabilityGetFolderTree": False,
            "capabilityMultifiling": False,
            "capabilityUnfiling": False,
            "capabilityVersionSpecificFiling": False,
            "capabilityPWCSearchable": False,
            "capabilityPWCUpdatable": True,
            "capabilityAllVersionsSearchable": False,
            "capabilityQuery": "none",
            "capabilityJoin": "none",
            "capabilityACL": "none",
        },
    }


def _folder_envelope(folder) -> dict:
    return {
        "succinctProperties": folder_properties(folder, path=compute_folder_path(_tree, folder))
    }


def _document_envelope(document) -> dict:
    lock = _tree.get_lock(document.id)
    return {
        "succinctProperties": document_properties(
            document,
            is_checked_out=lock is not None,
            checked_out_by=lock.locked_by if lock else None,
        )
    }


def _object_envelope_for(resolved: ResolvedObject) -> dict:
    if resolved.kind == "folder":
        return _folder_envelope(resolved.folder)
    return _document_envelope(resolved.document)


# --- Lesen (GET, cmisselector) -----------------------------------------


def _default_selector(resolved: ResolvedObject) -> str:
    return "children" if resolved.kind == "folder" else "content"


def _get_children(resolved: ResolvedObject) -> dict:
    if resolved.kind != "folder":
        raise CmisError("invalidArgument", "cmisselector=children ist nur für Ordner gültig")
    folders, documents = _tree.list_children(resolved.folder.id)
    objects = [{"object": _folder_envelope(f)} for f in folders]
    objects += [{"object": _document_envelope(d)} for d in documents]
    return {"objects": objects, "hasMoreItems": False, "numItems": len(objects)}


def _get_content(resolved: ResolvedObject) -> Response:
    if resolved.kind != "document":
        raise CmisError("invalidArgument", "cmisselector=content ist nur für Dokumente gültig")
    content = _tree.read_document_content(resolved.document.id)
    media_type = resolved.document.content_type or "application/octet-stream"
    return Response(content=content, media_type=media_type)


def _handle_read(repository_id: str, path: str, cmisselector: str | None, object_id: str | None):
    _check_license("read")
    _require_repository(repository_id)
    resolved = resolve_object(_tree, path=path, object_id=object_id)
    selector = (cmisselector or _default_selector(resolved)).lower()
    if selector == "children":
        return _get_children(resolved)
    if selector == "object":
        return _object_envelope_for(resolved)
    if selector == "content":
        return _get_content(resolved)
    raise CmisError("notSupported", f"cmisselector {selector!r} wird nicht unterstützt")


# --- Schreiben (POST, cmisaction) ---------------------------------------


def _extract_property(form, name: str) -> str | None:
    index = 0
    while f"propertyId[{index}]" in form:
        if form[f"propertyId[{index}]"] == name:
            return form.get(f"propertyValue[{index}]")
        index += 1
    return None


def _require_name(form) -> str:
    name = _extract_property(form, "cmis:name")
    if not name:
        raise CmisError("invalidArgument", "cmis:name ist erforderlich")
    return name


def _require_content(form) -> UploadFile:
    content = form.get("content")
    if content is None or isinstance(content, str):
        raise CmisError("invalidArgument", "content (Datei) ist erforderlich")
    return content


def _do_create_document(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "folder":
        raise CmisError("invalidArgument", "createDocument braucht einen Ordner als Ziel")
    name = _require_name(form)
    upload = _require_content(form)
    document = _tree.write_document(
        folder_id=target.folder.id,
        filename=name,
        content=upload.file.read(),
        content_type=upload.content_type,
        created_by=actor,
    )
    return _document_envelope(document), 201


def _do_create_folder(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "folder":
        raise CmisError("invalidArgument", "createFolder braucht einen Ordner als Ziel")
    name = _require_name(form)
    try:
        folder = _tree.create_folder(parent_id=target.folder.id, name=name, created_by=actor)
    except PathNotFoundError as exc:
        raise CmisError("objectNotFound", str(exc)) from exc
    return _folder_envelope(folder), 201


def _do_update(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    # Diese Referenzimplementierung bildet ausschliesslich `cmis:name` auf
    # ein DMS-Attribut ab (Umbenennen) - eigene Objekttyp-Attribute werden
    # nicht als CMIS-Properties exponiert, siehe "Bewusste Grenzen".
    name = _extract_property(form, "cmis:name")
    if name is None:
        return _object_envelope_for(target), 200
    if target.kind == "folder":
        folder = _tree.move_folder(target.folder.id, new_name=name)
        return _folder_envelope(folder), 200
    document = _tree.move_document(target.document.id, new_title=name)
    return _document_envelope(document), 200


def _do_move(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    target_folder_id = form.get("targetFolderId")
    if not target_folder_id:
        raise CmisError("invalidArgument", "targetFolderId ist erforderlich")
    try:
        if target.kind == "folder":
            folder = _tree.move_folder(target.folder.id, new_parent_id=target_folder_id)
            return _folder_envelope(folder), 201
        document = _tree.move_document(target.document.id, new_folder_id=target_folder_id)
        return _document_envelope(document), 201
    except PathNotFoundError as exc:
        raise CmisError("objectNotFound", str(exc)) from exc


def _do_delete(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind == "folder":
        # `folder-service`s Hard-Delete prüft nur eigene Unterordner, nicht
        # Dokumente (die in einem anderen Service/Schema leben, siehe
        # docs/services/cmis-connector.md "Bewusste Grenzen") - CMIS'
        # `delete` MUSS aber bei JEDEM Kind (Ordner ODER Dokument)
        # ablehnen, daher die vollständige Prüfung hier statt dort.
        subfolders, documents = _tree.list_children(target.folder.id)
        if subfolders or documents:
            raise CmisError("constraint", "Ordner ist nicht leer")
        _tree.delete_folder(target.folder.id)
    else:
        _tree.delete_document(target.document.id, deleted_by=actor)
    return {}, 200


def _cascade_delete_folder(folder, actor: str) -> None:
    folders, documents = _tree.list_children(folder.id)
    for document in documents:
        _tree.delete_document(document.id, deleted_by=actor)
    for child in folders:
        _cascade_delete_folder(child, actor)
    _tree.delete_folder(folder.id)


def _do_delete_tree(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "folder":
        raise CmisError("invalidArgument", "deleteTree ist nur für Ordner gültig")
    _cascade_delete_folder(target.folder, actor)
    return {}, 200


def _do_set_content(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "document":
        raise CmisError("invalidArgument", "setContent ist nur für Dokumente gültig")
    upload = _require_content(form)
    document = _tree.write_document(
        folder_id=target.document.folder_id,
        filename=target.document.title,
        content=upload.file.read(),
        content_type=upload.content_type,
        created_by=actor,
        existing_document_id=target.document.id,
        expected_base_version_number=target.document.current_version_number,
    )
    return _document_envelope(document), 201


def _do_check_out(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    """Kein separates PWC-Objekt: `document-service` kennt keine "Working
    Copy" als eigenständige Entität - die reale document-service-Sperre
    (4.2) übernimmt exakt die Rolle, die CMIS dem PWC-Mechanismus zuschreibt
    (kein Schreibzugriff durch andere bis Checkin/CancelCheckout). Die
    zurückgegebene "PWC"-Objekt-Id ist deshalb bewusst identisch zur
    Original-Dokument-Id, siehe docs/services/cmis-connector.md."""
    if target.kind != "document":
        raise CmisError("invalidArgument", "checkOut ist nur für Dokumente gültig")
    try:
        _tree.acquire_lock(target.document.id, locked_by=actor, session_id=f"cmis:{actor}")
    except LockConflictError as exc:
        raise CmisError("updateConflict", "Dokument ist bereits ausgecheckt") from exc
    return {
        "succinctProperties": document_properties(
            target.document, is_checked_out=True, checked_out_by=actor
        )
    }, 201


def _do_cancel_check_out(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "document":
        raise CmisError("invalidArgument", "cancelCheckOut ist nur für Dokumente gültig")
    _tree.release_lock(target.document.id, released_by=actor)
    return {}, 200


def _do_check_in(target: ResolvedObject, form, actor: str) -> tuple[dict, int]:
    if target.kind != "document":
        raise CmisError("invalidArgument", "checkIn ist nur für Dokumente gültig")
    upload = form.get("content")
    if upload is None or isinstance(upload, str):
        # Bewusste Grenze: `document-service`s Versions-Endpunkt verlangt je
        # Version zwingend eine Datei (`UploadFile = File(...)`) - ein
        # inhaltsloses Checkin (nur Metadaten/Kommentar) ist in dieser
        # Referenzimplementierung deshalb nicht möglich.
        raise CmisError(
            "invalidArgument",
            "checkIn ohne neuen Inhalt wird nicht unterstützt (document-service "
            "verlangt Dateiinhalt je Version)",
        )
    comment = form.get("checkinComment")
    try:
        document = _tree.write_document(
            folder_id=target.document.folder_id,
            filename=target.document.title,
            content=upload.file.read(),
            content_type=upload.content_type,
            created_by=actor,
            existing_document_id=target.document.id,
            expected_base_version_number=target.document.current_version_number,
            comment=comment if isinstance(comment, str) else None,
        )
    finally:
        _tree.release_lock(target.document.id, released_by=actor)
    return _document_envelope(document), 201


_ACTIONS: dict[str, Callable[[ResolvedObject, object, str], tuple[dict, int]]] = {
    "createdocument": _do_create_document,
    "createfolder": _do_create_folder,
    "update": _do_update,
    "move": _do_move,
    "delete": _do_delete,
    "deletetree": _do_delete_tree,
    "setcontent": _do_set_content,
    "checkout": _do_check_out,
    "cancelcheckout": _do_cancel_check_out,
    "checkin": _do_check_in,
}


def _dispatch_write(
    handler, path: str, object_id: str | None, form, actor: str
) -> tuple[dict, int]:
    _check_license("write")
    target = resolve_object(_tree, path=path, object_id=object_id)
    return handler(target, form, actor)


async def _handle_write(
    repository_id: str, path: str, url_object_id: str | None, request: Request, actor: str
) -> JSONResponse:
    """`await request.form()` ist eine Starlette-`async`-only-API (streamt
    den Multipart-Body), der eigentliche `DmsTreeClient`-Aufruf ist dagegen
    bewusst synchron (siehe dessen Docstring) - `asyncio.to_thread()` lagert
    ihn aus dem Event-Loop-Thread aus, statt ihn direkt in diesem `async def`
    zu blockieren (gleiches, bei ADR 0034 als künftiger Präzedenzfall
    festgehaltenes Muster).

    `objectId` kann sowohl als URL-Query-Parameter (5.3.4, adressiert bei
    `createDocument`/`createFolder` den Zielordner der Anfrage-URL selbst)
    als auch als Formular-Control (5.4.4.3.3, adressiert bei `update`/
    `move`/`delete`/... das zu bearbeitende Objekt) übergeben werden - das
    Formular-Control hat Vorrang, falls beide vorkommen."""
    _require_repository(repository_id)
    form = await request.form()
    action = str(form.get("cmisaction") or "").strip().lower()
    if not action:
        raise CmisError("invalidArgument", "cmisaction fehlt")
    handler = _ACTIONS.get(action)
    if handler is None:
        raise CmisError("notSupported", f"cmisaction {action!r} wird nicht unterstützt")
    form_object_id = form.get("objectId")
    object_id = (
        form_object_id if isinstance(form_object_id, str) and form_object_id else url_object_id
    )
    result, status_code = await asyncio.to_thread(
        _dispatch_write, handler, path, object_id, form, actor
    )
    return JSONResponse(status_code=status_code, content=result)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        capabilities=_DESCRIPTOR.as_capability_list(),
    )

    logger.info("cmis_connector_startup_completed")
    yield

    if registration:
        await registration.stop()
    _tree.close()
    _license_client.close()
    _auth_client.close()


app = FastAPI(title=settings.service_name, lifespan=lifespan)
register_exception_handlers(app)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/browser")
def get_repositories(actor: str = Depends(require_actor)) -> dict:
    _check_license("read")
    return {settings.cmis_repository_id: _repository_info()}


@app.get("/browser/{repository_id}")
def get_repository(
    repository_id: str, cmisselector: str | None = None, actor: str = Depends(require_actor)
) -> dict:
    _check_license("read")
    _require_repository(repository_id)
    return _repository_info()


@app.get("/browser/{repository_id}/root")
def get_root_object(
    repository_id: str,
    cmisselector: str | None = None,
    objectId: str | None = None,
    actor: str = Depends(require_actor),
):
    return _handle_read(repository_id, "", cmisselector, objectId)


@app.get("/browser/{repository_id}/root/{path:path}")
def get_object_by_path(
    repository_id: str,
    path: str,
    cmisselector: str | None = None,
    objectId: str | None = None,
    actor: str = Depends(require_actor),
):
    return _handle_read(repository_id, path, cmisselector, objectId)


@app.post("/browser/{repository_id}/root")
async def post_root_object(
    repository_id: str,
    request: Request,
    objectId: str | None = None,
    actor: str = Depends(require_actor),
) -> JSONResponse:
    return await _handle_write(repository_id, "", objectId, request, actor)


@app.post("/browser/{repository_id}/root/{path:path}")
async def post_object_by_path(
    repository_id: str,
    path: str,
    request: Request,
    objectId: str | None = None,
    actor: str = Depends(require_actor),
) -> JSONResponse:
    return await _handle_write(repository_id, path, objectId, request, actor)
