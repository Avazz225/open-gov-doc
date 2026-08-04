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
  renameFolder as apiRenameFolder,
  trashDocument as apiTrashDocument,
  trashFolder as apiTrashFolder,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { ApprovalsPane } from "./ApprovalsPane";
import { ExplorerPane } from "./ExplorerPane";
import { FavoritesPane } from "./FavoritesPane";
import { IconRail, type WorkspaceView } from "./IconRail";
import { MetadataPanel } from "./MetadataPanel";
import { PreviewPane } from "./PreviewPane";
import { SearchPane } from "./SearchPane";
import { Splitter } from "./Splitter";

const MIN_LEFT_WIDTH = 260;
const MIN_TOP_HEIGHT = 160;
const MIN_PANE_REMAINDER = 200;

function usePersistedSize(key: string, initial: number): [number, (value: number) => void] {
  const [size, setSize] = useState(initial);

  useEffect(() => {
    const stored = window.localStorage.getItem(key);
    if (stored) setSize(Number(stored));
  }, [key]);

  const update = useCallback(
    (value: number) => {
      setSize(value);
      window.localStorage.setItem(key, String(value));
    },
    [key]
  );

  return [size, update];
}

// Ersetzt seit P4-S4 die frühere flache `FolderBrowser` (P4-S2): dreigeteiltes
// Layout gemäß Nutzer-Feedback nach dem ersten echten Browser-Test des MVP
// (Konzept 8) - oben links Explorer mit Dokumententabs, unten links
// Metadaten-Panel, rechts Vorschau, alle drei über die Tab-Auswahl
// synchronisiert und per Ziehgriff (`Splitter`) in der Größe veränderbar.
// Ganz links außerhalb des Main-Contents die iconbasierte Navigationsleiste.
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
  const [tabs, setTabs] = useState<DocumentSummary[]>([]);
  const [activeTabId, setActiveTabId] = useState<string | null>(null);
  const [view, setView] = useState<WorkspaceView>("documents");

  const outerRef = useRef<HTMLDivElement>(null);
  const leftColRef = useRef<HTMLDivElement>(null);
  const [leftWidth, setLeftWidth] = usePersistedSize("dms.explorer.leftWidth", 380);
  const [topHeight, setTopHeight] = usePersistedSize("dms.explorer.topHeight", 340);

  const currentFolder = trail[trail.length - 1];
  const activeDocument = tabs.find((tab) => tab.id === activeTabId) ?? null;

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

  // Baumansicht-Navigation (2.2a/8, P5b-S4): `FolderTree` kennt bereits den
  // vollständigen Vorfahren-Pfad des angeklickten Knotens (er musste
  // aufgeklappt werden, um sichtbar zu sein) und übergibt ihn direkt - siehe
  // ADR 0015, warum hier kein neuer Backend-Endpunkt für den vollständigen
  // Pfad nötig war. Ersetzt den gesamten Breadcrumb-Trail statt ihn nur zu
  // verlängern, da ein Baum-Klick anders als `openFolder` nicht zwingend eine
  // Ebene tiefer als der aktuelle Trail führt.
  function navigateToFolder(path: Folder[]) {
    setTrail([
      { id: "root", name: t("folderBrowser.rootLabel") },
      ...path.map((folder) => ({ id: folder.id, name: folder.name })),
    ]);
  }

  function goToBreadcrumb(index: number) {
    setTrail((prev) => prev.slice(0, index + 1));
  }

  function openDocumentTab(doc: DocumentSummary) {
    setTabs((prev) => (prev.some((tab) => tab.id === doc.id) ? prev : [...prev, doc]));
    setActiveTabId(doc.id);
  }

  function selectTab(id: string) {
    setActiveTabId(id);
  }

  function closeTab(id: string) {
    setTabs((prev) => {
      const next = prev.filter((tab) => tab.id !== id);
      if (activeTabId === id) {
        setActiveTabId(next.length > 0 ? next[next.length - 1].id : null);
      }
      return next;
    });
  }

  function handleMetadataSaved(updated: DocumentSummary) {
    setTabs((prev) => prev.map((tab) => (tab.id === updated.id ? updated : tab)));
    setDocuments((prev) => prev.map((doc) => (doc.id === updated.id ? updated : doc)));
  }

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

  async function handleDeleteFolder(folderId: string): Promise<"trashed" | "pending_approval" | false> {
    if (!accessToken) return false;
    try {
      // Seit P7-S1b: regulärer Papierkorb-Weg statt sofortigem Hard-Delete
      // (kaskadiert über Unterordner + enthaltene Dokumente, siehe
      // docs/services/folder-service.md). Seit P7-S1c optional per
      // Vier-Augen-Prinzip zurückgestellt (`document.delete`/`folder.
      // delete`, siehe ExplorerPane) - nur bei sofortiger Ausführung neu
      // laden, ein Löschantrag ändert an der Liste noch nichts.
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

  // "Öffnen" aus der Favoriten-Merkliste (schnelles Wiederfinden, seit
  // P7-S1d). Dokument: `openDocumentTab` ist bereits unabhängig vom aktuell
  // angezeigten Ordner, keine Navigation nötig. Ordner: läuft die
  // `parent_id`-Kette clientseitig über den bestehenden `GET /folders/{id}`
  // bis zur Wurzel (`"root"`) hoch, um den vollständigen Breadcrumb-Pfad für
  // `navigateToFolder` (P5b-S4) zu rekonstruieren - kein neuer
  // Backend-Endpunkt dafür nötig.
  function handleOpenFavoriteDocument(doc: DocumentSummary) {
    openDocumentTab(doc);
    setView("documents");
  }

  async function handleOpenFavoriteFolder(folder: Folder) {
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

  function handleVerticalResize(offsetPx: number) {
    const rect = outerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const max = rect.width - MIN_PANE_REMAINDER;
    setLeftWidth(Math.min(Math.max(offsetPx, MIN_LEFT_WIDTH), Math.max(max, MIN_LEFT_WIDTH)));
  }

  function handleHorizontalResize(offsetPx: number) {
    const rect = leftColRef.current?.getBoundingClientRect();
    if (!rect) return;
    const max = rect.height - MIN_PANE_REMAINDER;
    setTopHeight(Math.min(Math.max(offsetPx, MIN_TOP_HEIGHT), Math.max(max, MIN_TOP_HEIGHT)));
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
        <div className="main-area" ref={outerRef} style={{ gridTemplateColumns: `${leftWidth}px 6px 1fr` }}>
          <div
            className="left-col"
            ref={leftColRef}
            style={{ gridTemplateRows: `${topHeight}px 6px 1fr` }}
          >
            {view === "documents" ? (
              <>
                <ExplorerPane
                  trail={trail}
                  folders={folders}
                  documents={documents}
                  isLoading={isLoading}
                  error={error}
                  tabs={tabs}
                  activeTabId={activeTabId}
                  onOpenFolder={openFolder}
                  onNavigateToFolder={navigateToFolder}
                  onBreadcrumbClick={goToBreadcrumb}
                  onOpenDocument={openDocumentTab}
                  onSelectTab={selectTab}
                  onCloseTab={closeTab}
                  onCreateFolder={handleCreateFolder}
                  onRenameFolder={handleRenameFolder}
                  onDeleteFolder={handleDeleteFolder}
                  onDeleteDocument={handleDeleteDocument}
                  token={accessToken ?? ""}
                  createdBy={user?.username ?? ""}
                  currentFolderId={currentFolder.id}
                  onUploaded={() => load(currentFolder.id)}
                />
                <Splitter
                  orientation="horizontal"
                  containerRef={leftColRef}
                  onResize={handleHorizontalResize}
                  label={t("explorer.resizeVertical")}
                />
                <MetadataPanel document={activeDocument} onSaved={handleMetadataSaved} />
              </>
            ) : view === "search" ? (
              <SearchPane token={accessToken ?? ""} onOpenDocument={openDocumentTab} />
            ) : view === "approvals" ? (
              <ApprovalsPane token={accessToken ?? ""} currentUsername={user?.username ?? ""} />
            ) : (
              <FavoritesPane
                token={accessToken ?? ""}
                currentUsername={user?.username ?? ""}
                onOpenDocument={handleOpenFavoriteDocument}
                onOpenFolder={handleOpenFavoriteFolder}
              />
            )}
          </div>
          <Splitter
            orientation="vertical"
            containerRef={outerRef}
            onResize={handleVerticalResize}
            label={t("explorer.resizeHorizontal")}
          />
          <PreviewPane document={activeDocument} />
        </div>
      </div>
    </div>
  );
}
