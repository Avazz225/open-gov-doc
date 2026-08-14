"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type Folder,
  createFolder as apiCreateFolder,
  getFolder as apiGetFolder,
  listChildFolders,
  listDocumentsInFolder,
  moveFolder as apiMoveFolder,
  renameFolder as apiRenameFolder,
  trashDocument as apiTrashDocument,
  trashFolder as apiTrashFolder,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { ApprovalsPane } from "./ApprovalsPane";
import { DockableDocumentArea, type DockableDocumentAreaHandle } from "./DockableDocumentArea";
import { FavoritesPane } from "./FavoritesPane";
import { IconRail, type WorkspaceView } from "./IconRail";
import { SearchPane } from "./SearchPane";
import { DelegationsPane } from "./DelegationsPane";
import { TeamspacesPane } from "./TeamspacesPane";
import { AussonderungPane } from "./AussonderungPane";
import { KontaktePane } from "./KontaktePane";
import { PoststellePane } from "./PoststellePane";
import { QuarantinePane } from "./QuarantinePane";
import { TrashPane } from "./TrashPane";
import { VorlagenPane } from "./VorlagenPane";

// Since P4-S4, replaces the earlier flat `FolderBrowser` (P4-S2). Until
// P16-S1 a fixed, hand-built splitter three-column layout (concept 8,
// factory default) - since P16-S1 `DockableDocumentArea` (real VS-Code-style
// docking via `dockview-react`, see ADR 0057/P16-S0) takes over the entire
// explorer/document tabs/preview/metadata area. Stays mounted at all times
// (only hidden via CSS when an IconRail special area is active) - unmounting
// and remounting would unnecessarily discard both the dockview layout and
// open document panels. Which documents are open deliberately stays here in
// `DocumentWorkspace` (not in `DockableDocumentArea` itself), so that this
// state survives a switch to a special area and back. Furthest to the left,
// outside the main content, the icon-based navigation rail.
export function DocumentWorkspace() {
  const { user, accessToken, logout } = useAuth();
  const { t } = useI18n();

  const [trail, setTrail] = useState<Array<Pick<Folder, "id" | "name">>>(() => [
    { id: "root", name: t("folderBrowser.rootLabel") },
  ]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openDocuments, setOpenDocuments] = useState<DocumentSummary[]>([]);
  const [view, setView] = useState<WorkspaceView>("documents");
  const dockableAreaRef = useRef<DockableDocumentAreaHandle>(null);
  // Version bump counter per document ID (P23-S7): `SignaturesPanel` and
  // `PreviewPane` are independently mounted dockview siblings
  // (`MetadataPanelContent`/`DocumentPreviewContent` in
  // `DockableDocumentArea.tsx`) - a successful signature creates a new
  // document version server-side (ADR 0025), which `PreviewPane`'s version
  // selection would otherwise only notice after reopening the document. A
  // simple counter instead of a boolean, so that signing the same document
  // multiple times triggers a reload each time (a boolean set from `false`
  // to `true` would no longer cause a state change on the second signing).
  const [versionBumps, setVersionBumps] = useState<Record<string, number>>({});
  const bumpDocumentVersion = useCallback((documentId: string) => {
    setVersionBumps((prev) => ({ ...prev, [documentId]: (prev[documentId] ?? 0) + 1 }));
  }, []);

  const currentFolder = trail[trail.length - 1];

  const load = useCallback(
    async (folderId: string) => {
      if (!accessToken) return;
      setIsLoading(true);
      setError(null);
      try {
        const [childFolders, docs] = await Promise.all([
          listChildFolders(accessToken, folderId),
          listDocumentsInFolder(accessToken, folderId),
        ]);
        setFolders(childFolders);
        setDocuments(docs);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("folderBrowser.loadError"));
      } finally {
        setIsLoading(false);
      }
    },
    [accessToken, t]
  );

  useEffect(() => {
    load(currentFolder.id);
  }, [currentFolder.id, load]);

  function openFolder(folder: Folder) {
    setTrail((prev) => [...prev, { id: folder.id, name: folder.name }]);
  }

  // Tree view navigation (2.2a/8, P5b-S4): `FolderTree` already knows the
  // full ancestor path of the clicked node (it had to be expanded to become
  // visible) and passes it directly - see ADR 0015 for why no new backend
  // endpoint for the full path was needed here. Replaces the entire
  // breadcrumb trail instead of merely extending it, since a tree click,
  // unlike `openFolder`, doesn't necessarily lead one level deeper than the
  // current trail.
  function navigateToFolder(path: Folder[]) {
    setTrail([
      { id: "root", name: t("folderBrowser.rootLabel") },
      ...path.map((folder) => ({ id: folder.id, name: folder.name })),
    ]);
  }

  function goToBreadcrumb(index: number) {
    setTrail((prev) => prev.slice(0, index + 1));
  }

  // Pure state updater (open-documents list) - passed through as
  // `onOpenDocument` to `DockableDocumentArea`, which assembles the complete
  // "open document" flow from it together with creating/activating the
  // corresponding dockview panel (see there). Kept separate from
  // `openDocumentTab` below, since `DockableDocumentArea`'s own ref handle
  // `openDocument` already calls this updater itself - calling
  // `openDocumentTab` at this point would create an infinite loop.
  const addOpenDocument = useCallback((doc: DocumentSummary) => {
    setOpenDocuments((prev) => (prev.some((d) => d.id === doc.id) ? prev : [...prev, doc]));
  }, []);

  const closeDocumentTab = useCallback((documentId: string) => {
    setOpenDocuments((prev) => prev.filter((d) => d.id !== documentId));
  }, []);

  // The single external entry point for opening a document (explorer,
  // search, favorites, teamspaces) - switches to the document view (so that
  // a document opened from a special area, e.g. search, actually becomes
  // visible - before P16-S1, a click in search left it open unnoticed in
  // the background, since the preview was only rendered within the document
  // view) and delegates to `DockableDocumentArea`'s ref handle, which
  // updates state + dockview panel together.
  const openDocumentTab = useCallback((doc: DocumentSummary) => {
    setView("documents");
    dockableAreaRef.current?.openDocument(doc);
  }, []);

  const handleMetadataSaved = useCallback((updated: DocumentSummary) => {
    setOpenDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
    setDocuments((prev) => prev.map((doc) => (doc.id === updated.id ? updated : doc)));
  }, []);

  async function handleCreateFolder(name: string, objectTypeId?: number): Promise<boolean> {
    if (!accessToken || !user) return false;
    try {
      await apiCreateFolder(accessToken, {
        name,
        parentId: currentFolder.id,
        createdBy: user.username,
        objectTypeId,
      });
      await load(currentFolder.id);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.createFolderError"));
      return false;
    }
  }

  async function handleRenameFolder(folderId: string, name: string): Promise<boolean> {
    if (!accessToken) return false;
    try {
      await apiRenameFolder(accessToken, folderId, name);
      await load(currentFolder.id);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.renameFolderError"));
      return false;
    }
  }

  // Folder move via drag & drop (8, P23-S4) - the endpoint already existed
  // (see `handleRenameFolder` above, same PATCH endpoint, just a different
  // field), here called from the UI for the first time.
  async function handleMoveFolder(folderId: string, newParentId: string): Promise<boolean> {
    if (!accessToken) return false;
    try {
      await apiMoveFolder(accessToken, folderId, newParentId);
      await load(currentFolder.id);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.moveFolderError"));
      return false;
    }
  }

  async function handleDeleteFolder(folderId: string): Promise<"trashed" | "pending_approval" | false> {
    if (!accessToken) return false;
    try {
      // Since P7-S1b: regular trash path instead of immediate hard delete
      // (cascades over subfolders + contained documents, see
      // docs/services/folder-service.md). Since P7-S1c optionally deferred
      // via the four-eyes principle (`document.delete`/`folder.
      // delete`, see ExplorerPane) - only reload on immediate execution, a
      // delete request doesn't change the list yet.
      const result = await apiTrashFolder(accessToken, folderId, user?.username ?? "");
      if (result.status === "trashed") await load(currentFolder.id);
      return result.status;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.deleteFolderError"));
      return false;
    }
  }

  async function handleDeleteDocument(
    documentId: string
  ): Promise<"trashed" | "pending_approval" | false> {
    if (!accessToken) return false;
    try {
      const result = await apiTrashDocument(accessToken, documentId, user?.username ?? "");
      if (result.status === "trashed") await load(currentFolder.id);
      return result.status;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.deleteDocumentError"));
      return false;
    }
  }

  // "Open" from the favorites bookmark list (quickly finding things again,
  // since P7-S1d). Document: `openDocumentTab` is already independent of the
  // currently displayed folder, no navigation needed. Folder: walks up the
  // `parent_id` chain client-side via the existing `GET /folders/{id}` up to
  // the root (`"root"`) in order to reconstruct the full breadcrumb path for
  // `navigateToFolder` (P5b-S4) - no new backend endpoint needed for this.
  function handleOpenFavoriteDocument(doc: DocumentSummary) {
    openDocumentTab(doc);
  }

  // Walks up the `parent_id` chain client-side via the existing `GET
  // /folders/{id}` up to the root (`"root"`) in order to reconstruct the
  // full breadcrumb path for `navigateToFolder` (P5b-S4) - no new backend
  // endpoint needed for this. Shared foundation for "open" from the
  // favorites bookmark list (P7-S1d) AND from a teamspace root folder
  // (P14-S6).
  async function openFolderPath(folder: Folder) {
    if (!accessToken) return;
    const path: Folder[] = [folder];
    let current = folder;
    while (current.parent_id && current.parent_id !== "root") {
      try {
        current = await apiGetFolder(accessToken, current.parent_id);
        path.unshift(current);
      } catch {
        break;
      }
    }
    navigateToFolder(path);
    setView("documents");
  }

  async function handleOpenFavoriteFolder(folder: Folder) {
    await openFolderPath(folder);
  }

  // "Open folder" from a teamspace (2.5, P14-S6) - the teamspace itself only
  // knows the `root_folder_id` (opaque reference), not the full `Folder`
  // object, so a `GET /folders/{id}` is loaded first here.
  async function handleOpenTeamspaceFolder(folderId: string) {
    if (!accessToken) return;
    try {
      const folder = await apiGetFolder(accessToken, folderId);
      await openFolderPath(folder);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("folderBrowser.loadError"));
    }
  }

  return (
    <div className="workspace">
      <div className="top-bar">
        <h1>{t("folderBrowser.title")}</h1>
        <div>
          {user && <span>{user.username} </span>}
          <button type="button" onClick={logout}>
            {t("common.logout")}
          </button>
        </div>
      </div>

      <div className="workspace-body">
        <IconRail activeView={view} onSelectView={setView} />
        <DockableDocumentArea
          ref={dockableAreaRef}
          hidden={view !== "documents"}
          trail={trail}
          folders={folders}
          documents={documents}
          isLoading={isLoading}
          error={error}
          onOpenFolder={openFolder}
          onNavigateToFolder={navigateToFolder}
          onBreadcrumbClick={goToBreadcrumb}
          onCreateFolder={handleCreateFolder}
          onRenameFolder={handleRenameFolder}
          onMoveFolder={handleMoveFolder}
          onDeleteFolder={handleDeleteFolder}
          onDeleteDocument={handleDeleteDocument}
          token={accessToken ?? ""}
          createdBy={user?.username ?? ""}
          currentFolderId={currentFolder.id}
          onUploaded={() => load(currentFolder.id)}
          openDocuments={openDocuments}
          onOpenDocument={addOpenDocument}
          onCloseDocument={closeDocumentTab}
          onMetadataSaved={handleMetadataSaved}
          versionBumps={versionBumps}
          onDocumentVersionBump={bumpDocumentVersion}
        />
        {view !== "documents" && (
          <div className="main-area-single">
            {view === "search" ? (
              <SearchPane token={accessToken ?? ""} onOpenDocument={openDocumentTab} />
            ) : view === "approvals" ? (
              <ApprovalsPane token={accessToken ?? ""} currentUsername={user?.username ?? ""} />
            ) : view === "favorites" ? (
              <FavoritesPane
                token={accessToken ?? ""}
                currentUsername={user?.username ?? ""}
                onOpenDocument={handleOpenFavoriteDocument}
                onOpenFolder={handleOpenFavoriteFolder}
              />
            ) : view === "teamspaces" ? (
              <TeamspacesPane
                token={accessToken ?? ""}
                currentPrincipalId={user?.sub ?? ""}
                onOpenFolder={handleOpenTeamspaceFolder}
              />
            ) : view === "delegations" ? (
              <DelegationsPane token={accessToken ?? ""} currentPrincipalId={user?.sub ?? ""} />
            ) : view === "trash" ? (
              <TrashPane token={accessToken ?? ""} />
            ) : view === "quarantine" ? (
              <QuarantinePane token={accessToken ?? ""} />
            ) : view === "poststelle" ? (
              <PoststellePane token={accessToken ?? ""} />
            ) : view === "aussonderung" ? (
              <AussonderungPane token={accessToken ?? ""} />
            ) : view === "vorlagen" ? (
              <VorlagenPane token={accessToken ?? ""} createdBy={user?.username ?? ""} />
            ) : (
              <KontaktePane token={accessToken ?? ""} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
