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

// Ersetzt seit P4-S4 die frühere flache `FolderBrowser` (P4-S2). Bis P16-S1
// ein festes, per Hand gebautes Splitter-Dreispaltenlayout (Konzept 8,
// Werksstandard) - seit P16-S1 übernimmt `DockableDocumentArea` (echtes
// VS-Code-artiges Docking über `dockview-react`, siehe ADR 0057/P16-S0) den
// gesamten Explorer/Dokumenttabs/Vorschau/Metadaten-Bereich. Bleibt dabei
// immer gemountet (nur per CSS ausgeblendet, wenn ein IconRail-Sonderbereich
// aktiv ist) - ein Aus-/Wiedereinhängen würde sowohl das dockview-Layout als
// auch offene Dokument-Panels unnötig verwerfen. Welche Dokumente offen sind
// bleibt bewusst hier in `DocumentWorkspace` (nicht in `DockableDocumentArea`
// selbst), damit dieser Zustand einen Wechsel zu einem Sonderbereich und
// zurück übersteht. Ganz links außerhalb des Main-Contents die iconbasierte
// Navigationsleiste.
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

  // Reiner Zustands-Updater (offene-Dokumente-Liste) - wird als `onOpenDocument`
  // an `DockableDocumentArea` durchgereicht, die daraus zusammen mit dem
  // Anlegen/Aktivieren des zugehörigen dockview-Panels den vollständigen
  // "Dokument öffnen"-Ablauf zusammensetzt (siehe dort). Getrennt von
  // `openDocumentTab` unten, da `DockableDocumentArea`s eigener Ref-Handle
  // `openDocument` diesen Updater bereits selbst aufruft - ein Aufruf von
  // `openDocumentTab` an dieser Stelle würde eine Endlosschleife erzeugen.
  const addOpenDocument = useCallback((doc: DocumentSummary) => {
    setOpenDocuments((prev) => (prev.some((d) => d.id === doc.id) ? prev : [...prev, doc]));
  }, []);

  const closeDocumentTab = useCallback((documentId: string) => {
    setOpenDocuments((prev) => prev.filter((d) => d.id !== documentId));
  }, []);

  // Einziger externer Einstiegspunkt zum Öffnen eines Dokuments (Explorer,
  // Suche, Favoriten, Teamspaces) - schaltet auf die Dokumentenansicht um
  // (damit ein aus einem Sonderbereich, z. B. der Suche, geöffnetes Dokument
  // auch tatsächlich sichtbar wird - vor P16-S1 blieb es bei einem Klick in
  // der Suche unbemerkt im Hintergrund offen, da die Vorschau nur innerhalb
  // der Dokumentenansicht gerendert wurde) und delegiert an den Ref-Handle
  // von `DockableDocumentArea`, die Zustand + dockview-Panel gemeinsam aktualisiert.
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

  // Ordner-Verschieben per Drag & Drop (8, P23-S4) - der Endpunkt existierte
  // bereits (siehe `handleRenameFolder` oben, gleicher PATCH-Endpunkt, nur ein
  // anderes Feld), hier nur erstmals aus der UI heraus aufgerufen.
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
  }

  // Läuft die `parent_id`-Kette clientseitig über den bestehenden `GET
  // /folders/{id}` bis zur Wurzel (`"root"`) hoch, um den vollständigen
  // Breadcrumb-Pfad für `navigateToFolder` (P5b-S4) zu rekonstruieren - kein
  // neuer Backend-Endpunkt dafür nötig. Gemeinsame Grundlage für "Öffnen" aus
  // der Favoriten-Merkliste (P7-S1d) UND aus einem Teamspace-Wurzelordner
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

  // "Ordner öffnen" aus einem Teamspace (2.5, P14-S6) - der Teamspace selbst
  // kennt nur die `root_folder_id` (opake Referenz), nicht das vollständige
  // `Folder`-Objekt, daher hier zunächst ein `GET /folders/{id}` nachladen.
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
