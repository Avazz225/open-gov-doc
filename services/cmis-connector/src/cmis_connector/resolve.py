"""Object-URL-Auflösung (5.3.4 "Object URLs"): ein Objekt wird entweder per
`objectId`-Query-Parameter (hat Vorrang) oder per Pfad adressiert, der an die
Root-Folder-URL angehängt ist. `DmsTreeClient` kann bereits vorwärts (Pfad
-> Objekt) auflösen (`resolve_path`) - für den umgekehrten Weg (Id -> voller
CMIS-Pfad, gebraucht für `cmis:path`, 2.1.5.3) läuft dieses Modul die
`parent_id`-Kette bis zur Wurzel hoch."""

from dataclasses import dataclass
from typing import Literal

from dms_connector_sdk import DmsTreeClient, PathNotFoundError, TreeDocument, TreeFolder

from cmis_connector.errors import CmisError

ObjectKind = Literal["folder", "document"]


@dataclass(frozen=True)
class ResolvedObject:
    kind: ObjectKind
    folder: TreeFolder | None
    document: TreeDocument | None
    path: str

    @property
    def id(self) -> str:
        return self.folder.id if self.kind == "folder" else self.document.id  # type: ignore[union-attr]


def compute_folder_path(tree: DmsTreeClient, folder: TreeFolder) -> str:
    if folder.id == tree.root_folder_id:
        return "/"
    segments = [folder.name]
    parent_id = folder.parent_id
    while parent_id is not None and parent_id != tree.root_folder_id:
        parent = tree.get_folder(parent_id)
        segments.append(parent.name)
        parent_id = parent.parent_id
    return "/" + "/".join(reversed(segments))


def resolve_by_id(tree: DmsTreeClient, object_id: str) -> ResolvedObject:
    try:
        folder = tree.get_folder(object_id)
    except PathNotFoundError:
        pass
    else:
        return ResolvedObject(
            kind="folder", folder=folder, document=None, path=compute_folder_path(tree, folder)
        )
    try:
        document = tree.get_document(object_id)
    except PathNotFoundError as exc:
        raise CmisError("objectNotFound", f"Kein Objekt mit id {object_id!r}") from exc
    parent = tree.get_folder(document.folder_id) if document.folder_id else None
    parent_path = compute_folder_path(tree, parent) if parent else "/"
    document_path = parent_path.rstrip("/") + "/" + document.title
    return ResolvedObject(kind="document", folder=None, document=document, path=document_path)


def resolve_by_path(tree: DmsTreeClient, path: str) -> ResolvedObject:
    try:
        node = tree.resolve_path(path)
    except PathNotFoundError as exc:
        raise CmisError("objectNotFound", f"Kein Objekt unter Pfad {path!r}") from exc
    if isinstance(node, TreeFolder):
        return ResolvedObject(
            kind="folder", folder=node, document=None, path=compute_folder_path(tree, node)
        )
    return ResolvedObject(kind="document", folder=None, document=node, path="/" + path.strip("/"))


def resolve_object(tree: DmsTreeClient, *, path: str, object_id: str | None) -> ResolvedObject:
    """`objectId` hat Vorrang vor dem Pfad (5.3.4, wörtlich: "If the
    parameter objectId is set, it takes precedence over the path")."""
    if object_id:
        return resolve_by_id(tree, object_id)
    return resolve_by_path(tree, path)
