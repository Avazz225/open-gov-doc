"use client";

import { useCallback, useEffect, useState, type FormEvent, type MouseEvent as ReactMouseEvent } from "react";
import { useI18n } from "@/i18n";
import {
  addFavorite,
  getApprovalConfig,
  getKennzeichenConfig,
  getShareLinkConfig,
  listDeletedDocuments,
  listDeletedFolders,
  listFavorites,
  listObjectTypes,
  removeFavorite,
  restoreDocument,
  restoreFolder,
  type DocumentSummary,
  type Folder,
  type ObjectType,
} from "@/lib/api";
import { folderIcon } from "@/lib/icons";
import { formatDocumentTitle } from "@/lib/kennzeichen";
import { BulkEditModal, type BulkEditItem } from "./BulkEditModal";
import { ContextMenu, type ContextMenuItem } from "./ContextMenu";
import { FolderRetentionModal } from "./FolderRetentionModal";
import { FolderTree } from "./FolderTree";
import { ShareLinkModal } from "./ShareLinkModal";
import { UploadForm } from "./UploadForm";

export interface BreadcrumbEntry {
  id: string;
  name: string;
}

const VIEW_MODE_KEY = "dms.explorer.viewMode";
type ExplorerViewMode = "list" | "tree";

function loadViewMode(): ExplorerViewMode {
  if (typeof window === "undefined") return "list";
  return window.localStorage.getItem(VIEW_MODE_KEY) === "tree" ? "tree" : "list";
}

// Eigenständiges Panel im dockbaren Arbeitsbereich (Konzept 8, seit P16-S1) -
// Windows-Explorer-artige Ordnernavigation mit Ordner-CRUD (bisher gab es nur
// Navigation, keine Erstellung/Umbenennung/Löschung in der UI - die Backend-
// Endpunkte existierten bereits seit P3-S3). Trug bis P16-S1 zusätzlich die
// Tableiste für geöffnete Dokumente - die wandert seit P16-S1 mit der
// Vorschau zusammen in eine eigene dockview-Gruppe (`DockableDocumentArea`,
// "Dokumenttabs künftig über der Vorschau statt über dem Explorer", Konzept
// 8) und wird jetzt von dockviews eigenem Tab-Strip gerendert statt einer
// handgebauten Tableiste hier.
export function ExplorerPane({
  trail,
  folders,
  documents,
  isLoading,
  error,
  onOpenFolder,
  onNavigateToFolder,
  onBreadcrumbClick,
  onOpenDocument,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
  onDeleteDocument,
  token,
  createdBy,
  currentFolderId,
  onUploaded,
}: {
  trail: BreadcrumbEntry[];
  folders: Folder[];
  documents: DocumentSummary[];
  isLoading: boolean;
  error: string | null;
  onOpenFolder: (folder: Folder) => void;
  onNavigateToFolder: (path: Folder[]) => void;
  onBreadcrumbClick: (index: number) => void;
  onOpenDocument: (doc: DocumentSummary) => void;
  onCreateFolder: (name: string, objectTypeId?: number) => Promise<boolean>;
  onRenameFolder: (folderId: string, name: string) => Promise<boolean>;
  onDeleteFolder: (folderId: string) => Promise<"trashed" | "pending_approval" | false>;
  onDeleteDocument: (documentId: string) => Promise<"trashed" | "pending_approval" | false>;
  token: string;
  createdBy: string;
  currentFolderId: string;
  onUploaded: () => void;
}) {
  const { t } = useI18n();
  const [isCreatingFolder, setIsCreatingFolder] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [renamingFolderId, setRenamingFolderId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [showUpload, setShowUpload] = useState(false);
  const [viewMode, setViewMode] = useState<ExplorerViewMode>(loadViewMode);
  const [folderIconById, setFolderIconById] = useState<Record<number, string | null>>({});
  const [folderObjectTypes, setFolderObjectTypes] = useState<ObjectType[]>([]);
  const [newFolderObjectTypeId, setNewFolderObjectTypeId] = useState("");
  const [documentTypeById, setDocumentTypeById] = useState<Record<number, ObjectType>>({});
  const [kennzeichenShowByDefault, setKennzeichenShowByDefault] = useState(true);
  const [showTrash, setShowTrash] = useState(false);
  const [trashDocuments, setTrashDocuments] = useState<DocumentSummary[]>([]);
  const [trashFolders, setTrashFolders] = useState<Folder[]>([]);
  const [isTrashLoading, setIsTrashLoading] = useState(false);
  const [trashError, setTrashError] = useState<string | null>(null);
  const [retentionModalFolder, setRetentionModalFolder] = useState<Folder | null>(null);
  const [folderDeleteRequiresApproval, setFolderDeleteRequiresApproval] = useState(false);
  const [documentDeleteRequiresApproval, setDocumentDeleteRequiresApproval] = useState(false);
  const [deleteMessage, setDeleteMessage] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; items: ContextMenuItem[] } | null>(
    null
  );
  const [favoriteKeys, setFavoriteKeys] = useState<Set<string>>(new Set());
  const [favoriteError, setFavoriteError] = useState<string | null>(null);
  const [shareLinkEnabled, setShareLinkEnabled] = useState(false);
  const [shareLinkModalDocument, setShareLinkModalDocument] = useState<DocumentSummary | null>(
    null
  );
  // Sammelbearbeitung von Metadaten (8, P14-S12) - "kind:id"-Set, gleiches
  // Schlüsselformat wie `favoriteKeys` oben. Nur in der Listenansicht
  // außerhalb des Papierkorbs sinnvoll (siehe Checkbox-Rendering unten) -
  // wird bei Ordnerwechsel/Papierkorb-Umschalten geleert, damit sie nie auf
  // gerade nicht mehr sichtbare Objekte verweist.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [showBulkEdit, setShowBulkEdit] = useState(false);

  useEffect(() => {
    setSelectedKeys(new Set());
  }, [currentFolderId, showTrash]);

  function toggleSelected(kind: "folder" | "document", id: string) {
    const key = `${kind}:${id}`;
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const selectedItems: BulkEditItem[] = [
    ...folders
      .filter((folder) => selectedKeys.has(`folder:${folder.id}`))
      .map((folder) => ({
        kind: "folder" as const,
        id: folder.id,
        name: folder.name,
        object_type_id: folder.object_type_id,
        attributes: folder.attributes,
      })),
    ...documents
      .filter((doc) => selectedKeys.has(`document:${doc.id}`))
      .map((doc) => ({
        kind: "document" as const,
        id: doc.id,
        name: formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault),
        object_type_id: doc.object_type_id,
        attributes: doc.attributes,
      })),
  ];

  // Klassen-Icons vor jedem Ordnernamen (2.2a/2.2b) sowie die Liste selbst
  // für die Ordnerklassen-Auswahl beim Anlegen (Nutzer-Feedback: bislang war
  // die Ordnerklasse beim Erstellen gar nicht wählbar, obwohl das Backend sie
  // seit P3-S3 unterstützt) - einmalig geladen, kein Blocker für die übrige
  // Anzeige, wenn das fehlschlägt (bleibt dann beim generischen
  // Ordner-Symbol/keiner Klassenauswahl, siehe `folderIcon()`-Fallback).
  useEffect(() => {
    if (!token) return;
    listObjectTypes(token, "folder")
      .then((types) => {
        setFolderIconById(Object.fromEntries(types.map((ot) => [ot.id, ot.icon])));
        setFolderObjectTypes(types);
      })
      .catch(() => {});
  }, [token]);

  // Kennzeichen-Anzeige vor dem Dateinamen (2.2/8, P5e-S3) - Objekttyp-Override
  // je Dokumentenart plus globaler Standard, siehe lib/kennzeichen.ts. Gleiches
  // "einmalig laden, bei Fehlschlag beim Fallback bleiben"-Muster wie oben.
  useEffect(() => {
    if (!token) return;
    listObjectTypes(token, "document")
      .then((types) => setDocumentTypeById(Object.fromEntries(types.map((ot) => [ot.id, ot]))))
      .catch(() => {});
    getKennzeichenConfig(token)
      .then((config) => setKennzeichenShowByDefault(config.show_before_filename))
      .catch(() => {});
  }, [token]);

  // Löschantrag-Workflow (5.2, seit P7-S1c): einmalig geladen, ob die
  // reguläre Papierkorb-Aktion per Vier-Augen-Prinzip genehmigungspflichtig
  // konfiguriert ist - bestimmt nur die Beschriftung ("Löschen" vs.
  // "Löschung beantragen"), die eigentliche Durchsetzung passiert serverseitig
  // in document-service/folder-service unabhängig davon. Bei Fehlschlag
  // (z. B. permission-service nicht erreichbar) bleibt der sichere Default
  // "false" - identisches Fallback-Prinzip wie bei den Klassen-Icons oben.
  useEffect(() => {
    if (!token) return;
    getApprovalConfig(token, "folder.delete")
      .then((config) => setFolderDeleteRequiresApproval(config.requires_approval))
      .catch(() => {});
    getApprovalConfig(token, "document.delete")
      .then((config) => setDocumentDeleteRequiresApproval(config.requires_approval))
      .catch(() => {});
  }, [token]);

  // Öffentlicher Freigabelink (4.2a, P14-S10): der Kontextmenü-Eintrag wird
  // nur angeboten, wenn die Funktion installationsweit aktiv ist ("kein
  // Menüpunkt" laut Konzept-Wortlaut bei Deaktivierung) - bei Fehlschlag
  // bleibt der sichere Default "false", gleiches Fallback-Prinzip wie bei
  // den übrigen einmalig geladenen Konfigurationen oben.
  useEffect(() => {
    if (!token) return;
    getShareLinkConfig(token)
      .then((config) => setShareLinkEnabled(config.enabled))
      .catch(() => {});
  }, [token]);

  // Favoriten/Merkliste (schnelles Wiederfinden, seit P7-S1d): einmalig alle
  // Favoriten des aktuellen Nutzers laden (beide Objekttypen in einem Aufruf,
  // `favorite-service` filtert nicht standardmäßig nach Typ), als
  // "type:id"-Set für O(1)-Mitgliedschaftsprüfung im Kontextmenü/⭐-Präfix.
  // Nach jeder Änderung neu geladen statt optimistisch aktualisiert, gleiches
  // "Quelle der Wahrheit bleibt der Server"-Prinzip wie beim Papierkorb.
  const reloadFavorites = useCallback(() => {
    if (!token || !createdBy) return;
    listFavorites(token, createdBy)
      .then((favorites) =>
        setFavoriteKeys(new Set(favorites.map((f) => `${f.object_type}:${f.object_id}`)))
      )
      .catch(() => {});
  }, [token, createdBy]);

  useEffect(() => {
    reloadFavorites();
  }, [reloadFavorites]);

  async function toggleFavorite(objectType: "document" | "folder", objectId: string) {
    if (!token || !createdBy) return;
    setFavoriteError(null);
    const key = `${objectType}:${objectId}`;
    try {
      if (favoriteKeys.has(key)) {
        await removeFavorite(token, { user_id: createdBy, object_type: objectType, object_id: objectId });
      } else {
        await addFavorite(token, { user_id: createdBy, object_type: objectType, object_id: objectId });
      }
      reloadFavorites();
    } catch {
      setFavoriteError(t("explorer.favoriteError"));
    }
  }

  // Papierkorb-Umschalter (5.2, seit P7-S1, um Ordner erweitert seit
  // P7-S1b) - bewusst minimal: nur der aktuelle Ordner, keine eigene
  // Sonderbereich-Navigation (siehe Phase 15).
  function reloadTrash() {
    if (!token) return;
    setIsTrashLoading(true);
    setTrashError(null);
    Promise.all([
      listDeletedDocuments(token, currentFolderId),
      listDeletedFolders(token, currentFolderId),
    ])
      .then(([documents, folders]) => {
        setTrashDocuments(documents);
        setTrashFolders(folders);
      })
      .catch(() => setTrashError(t("explorer.trashLoadError")))
      .finally(() => setIsTrashLoading(false));
  }

  useEffect(() => {
    if (!showTrash) return;
    reloadTrash();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showTrash, token, currentFolderId]);

  async function handleRestore(doc: DocumentSummary) {
    if (!token) return;
    try {
      await restoreDocument(token, doc.id);
      reloadTrash();
      onUploaded();
    } catch {
      setTrashError(t("explorer.trashRestoreError"));
    }
  }

  async function handleRestoreFolder(folder: Folder) {
    if (!token) return;
    try {
      await restoreFolder(token, folder.id);
      reloadTrash();
      onUploaded();
    } catch {
      setTrashError(t("explorer.trashRestoreFolderError"));
    }
  }

  function changeViewMode(mode: ExplorerViewMode) {
    setViewMode(mode);
    window.localStorage.setItem(VIEW_MODE_KEY, mode);
  }

  async function handleCreateSubmit(event: FormEvent) {
    event.preventDefault();
    if (!newFolderName.trim()) return;
    const objectTypeId = newFolderObjectTypeId ? Number(newFolderObjectTypeId) : undefined;
    const ok = await onCreateFolder(newFolderName.trim(), objectTypeId);
    if (ok) {
      setNewFolderName("");
      setNewFolderObjectTypeId("");
      setIsCreatingFolder(false);
    }
  }

  function startRename(folder: Folder) {
    setRenamingFolderId(folder.id);
    setRenameValue(folder.name);
  }

  async function handleRenameSubmit(event: FormEvent, folderId: string) {
    event.preventDefault();
    if (!renameValue.trim()) return;
    const ok = await onRenameFolder(folderId, renameValue.trim());
    if (ok) setRenamingFolderId(null);
  }

  async function handleDelete(folder: Folder) {
    const confirmText = folderDeleteRequiresApproval
      ? t("explorer.confirmRequestDeleteFolder", { name: folder.name })
      : t("explorer.confirmDeleteFolder", { name: folder.name });
    if (!window.confirm(confirmText)) return;
    setDeleteMessage(null);
    const result = await onDeleteFolder(folder.id);
    if (result === "pending_approval") setDeleteMessage(t("explorer.deleteRequestPending"));
  }

  async function handleDeleteDocument(doc: DocumentSummary) {
    const confirmText = documentDeleteRequiresApproval
      ? t("explorer.confirmRequestDeleteDocument", {
          name: formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault),
        })
      : t("explorer.confirmDeleteDocument", {
          name: formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault),
        });
    if (!window.confirm(confirmText)) return;
    setDeleteMessage(null);
    const result = await onDeleteDocument(doc.id);
    if (result === "pending_approval") setDeleteMessage(t("explorer.deleteRequestPending"));
  }

  function openFolderContextMenu(event: ReactMouseEvent, folder: Folder) {
    event.preventDefault();
    const isFavorite = favoriteKeys.has(`folder:${folder.id}`);
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      items: [
        {
          label: folderDeleteRequiresApproval
            ? t("explorer.requestDeleteFolder", { name: folder.name })
            : t("explorer.deleteFolder", { name: folder.name }),
          onSelect: () => handleDelete(folder),
        },
        {
          label: isFavorite
            ? t("explorer.removeFavorite", { name: folder.name })
            : t("explorer.addFavorite", { name: folder.name }),
          onSelect: () => toggleFavorite("folder", folder.id),
        },
      ],
    });
  }

  function openDocumentContextMenu(event: ReactMouseEvent, doc: DocumentSummary) {
    event.preventDefault();
    const name = formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault);
    const isFavorite = favoriteKeys.has(`document:${doc.id}`);
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      items: [
        {
          label: documentDeleteRequiresApproval
            ? t("explorer.requestDeleteDocument", { name })
            : t("explorer.deleteDocument", { name }),
          onSelect: () => handleDeleteDocument(doc),
        },
        {
          label: isFavorite ? t("explorer.removeFavorite", { name }) : t("explorer.addFavorite", { name }),
          onSelect: () => toggleFavorite("document", doc.id),
        },
        ...(shareLinkEnabled
          ? [
              {
                label: t("explorer.shareLink", { name }),
                onSelect: () => setShareLinkModalDocument(doc),
              },
            ]
          : []),
      ],
    });
  }

  return (
    <section className="explorer-pane" aria-label={t("explorer.paneLabel")}>
      {viewMode === "list" && (
        <nav className="breadcrumbs" aria-label={t("folderBrowser.breadcrumbLabel")}>
          {trail.map((entry, index) => (
            <span key={entry.id}>
              {index > 0 && <span className="separator"> / </span>}
              <button type="button" onClick={() => onBreadcrumbClick(index)}>
                {entry.name}
              </button>
            </span>
          ))}
        </nav>
      )}

      <div className="explorer-toolbar">
        <button type="button" onClick={() => setIsCreatingFolder((v) => !v)}>
          {t("explorer.newFolder")}
        </button>
        <button type="button" onClick={() => setShowUpload((v) => !v)}>
          {t("explorer.toggleUpload")}
        </button>
        <button
          type="button"
          aria-pressed={showTrash}
          className={showTrash ? "view-mode-active" : undefined}
          onClick={() => setShowTrash((v) => !v)}
        >
          {t("explorer.toggleTrash")}
        </button>
        <span className="view-mode-toggle" role="group" aria-label={t("explorer.viewModeLabel")}>
          <button
            type="button"
            className={viewMode === "list" ? "view-mode-active" : undefined}
            aria-pressed={viewMode === "list"}
            onClick={() => changeViewMode("list")}
          >
            {t("explorer.viewModeList")}
          </button>
          <button
            type="button"
            className={viewMode === "tree" ? "view-mode-active" : undefined}
            aria-pressed={viewMode === "tree"}
            onClick={() => changeViewMode("tree")}
          >
            {t("explorer.viewModeTree")}
          </button>
        </span>
      </div>

      {isCreatingFolder && (
        <form
          className="inline-form"
          aria-label={t("explorer.newFolderFormLabel")}
          onSubmit={handleCreateSubmit}
        >
          <input
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            placeholder={t("explorer.newFolderPlaceholder")}
            autoFocus
          />
          {folderObjectTypes.length > 0 && (
            <select
              aria-label={t("explorer.newFolderObjectTypeLabel")}
              value={newFolderObjectTypeId}
              onChange={(e) => setNewFolderObjectTypeId(e.target.value)}
            >
              <option value="">{t("explorer.newFolderNoObjectType")}</option>
              {folderObjectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>
                  {ot.name}
                </option>
              ))}
            </select>
          )}
          <button type="submit">{t("common.create")}</button>
          <button type="button" onClick={() => setIsCreatingFolder(false)}>
            {t("common.cancel")}
          </button>
        </form>
      )}

      {showUpload && (
        <UploadForm
          token={token}
          folderId={currentFolderId}
          createdBy={createdBy}
          onUploaded={onUploaded}
          onClose={() => setShowUpload(false)}
        />
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {deleteMessage && <p className="hint">{deleteMessage}</p>}

      {selectedKeys.size > 0 && (
        <div className="explorer-toolbar">
          <span className="hint">{t("bulkEdit.selectedCount", { count: selectedKeys.size })}</span>
          <button type="button" onClick={() => setShowBulkEdit(true)}>
            {t("bulkEdit.openButton")}
          </button>
          <button type="button" onClick={() => setSelectedKeys(new Set())}>
            {t("bulkEdit.clearSelection")}
          </button>
        </div>
      )}

      {favoriteError && (
        <p className="error-text" role="alert">
          {favoriteError}
        </p>
      )}

      {showTrash ? (
        <>
          {trashError && (
            <p className="error-text" role="alert">
              {trashError}
            </p>
          )}
          {isTrashLoading ? (
            <p>{t("common.loading")}</p>
          ) : trashDocuments.length === 0 && trashFolders.length === 0 ? (
            <p className="empty-state">{t("explorer.trashEmpty")}</p>
          ) : (
            <ul className="entry-list">
              {trashFolders.map((folder) => (
                <li className="entry-row" key={folder.id}>
                  <span className="entry-name">
                    {folderIcon(
                      folder.object_type_id !== null ? folderIconById[folder.object_type_id] : null
                    )}{" "}
                    {folder.name}
                  </span>
                  <span className="actions">
                    <button type="button" onClick={() => handleRestoreFolder(folder)}>
                      {t("explorer.restoreFolder")}
                    </button>
                  </span>
                </li>
              ))}
              {trashDocuments.map((doc) => (
                <li className="entry-row" key={doc.id}>
                  <span className="entry-name">
                    📄 {formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault)}
                  </span>
                  <span className="actions">
                    <button type="button" onClick={() => handleRestore(doc)}>
                      {t("explorer.restoreDocument")}
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : viewMode === "tree" ? (
        <FolderTree
          token={token}
          rootLabel={trail[0]?.name ?? t("folderBrowser.rootLabel")}
          folderIcons={folderIconById}
          documentTypeById={documentTypeById}
          kennzeichenShowByDefault={kennzeichenShowByDefault}
          onOpenDocument={onOpenDocument}
          onNavigateToFolder={onNavigateToFolder}
        />
      ) : isLoading ? (
        <p>{t("common.loading")}</p>
      ) : (
        <>
          {folders.length === 0 && documents.length === 0 && (
            <p className="empty-state">{t("folderBrowser.emptyFolder")}</p>
          )}
          <ul className="entry-list">
            {folders.map((folder) => (
              <li
                className="entry-row"
                key={folder.id}
                onContextMenu={(e) => openFolderContextMenu(e, folder)}
              >
                <input
                  type="checkbox"
                  aria-label={t("bulkEdit.selectItem", { name: folder.name })}
                  checked={selectedKeys.has(`folder:${folder.id}`)}
                  onChange={() => toggleSelected("folder", folder.id)}
                />
                {renamingFolderId === folder.id ? (
                  <form
                    className="inline-form"
                    aria-label={t("explorer.renameFolderFormLabel")}
                    onSubmit={(e) => handleRenameSubmit(e, folder.id)}
                  >
                    <input
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      autoFocus
                    />
                    <button type="submit">{t("common.save")}</button>
                    <button type="button" onClick={() => setRenamingFolderId(null)}>
                      {t("common.cancel")}
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      className="entry-name"
                      onClick={() => onOpenFolder(folder)}
                    >
                      {favoriteKeys.has(`folder:${folder.id}`) && "⭐ "}
                      {folderIcon(
                        folder.object_type_id !== null ? folderIconById[folder.object_type_id] : null
                      )}{" "}
                      {folder.name}
                    </button>
                    <span className="actions">
                      <button
                        type="button"
                        aria-label={t("explorer.renameFolder", { name: folder.name })}
                        onClick={() => startRename(folder)}
                      >
                        ✏️
                      </button>
                      <button
                        type="button"
                        aria-label={t("explorer.folderRetention", { name: folder.name })}
                        onClick={() => setRetentionModalFolder(folder)}
                      >
                        🕒
                      </button>
                    </span>
                  </>
                )}
              </li>
            ))}
            {documents.map((doc) => (
              <li
                className="entry-row"
                key={doc.id}
                onContextMenu={(e) => openDocumentContextMenu(e, doc)}
              >
                <input
                  type="checkbox"
                  aria-label={t("bulkEdit.selectItem", {
                    name: formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault),
                  })}
                  checked={selectedKeys.has(`document:${doc.id}`)}
                  onChange={() => toggleSelected("document", doc.id)}
                />
                <button
                  type="button"
                  className="entry-name"
                  onClick={() => onOpenDocument(doc)}
                >
                  {favoriteKeys.has(`document:${doc.id}`) && "⭐ "}
                  📄 {formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault)}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {retentionModalFolder && (
        <FolderRetentionModal
          folder={retentionModalFolder}
          onClose={() => setRetentionModalFolder(null)}
        />
      )}

      {shareLinkModalDocument && (
        <ShareLinkModal
          documentId={shareLinkModalDocument.id}
          documentTitle={formatDocumentTitle(
            shareLinkModalDocument,
            documentTypeById,
            kennzeichenShowByDefault
          )}
          onClose={() => setShareLinkModalDocument(null)}
        />
      )}

      {showBulkEdit && (
        <BulkEditModal
          token={token}
          items={selectedItems}
          onClose={() => setShowBulkEdit(false)}
          onDone={() => {
            setSelectedKeys(new Set());
            onUploaded();
          }}
        />
      )}

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenu.items}
          onClose={() => setContextMenu(null)}
        />
      )}
    </section>
  );
}
