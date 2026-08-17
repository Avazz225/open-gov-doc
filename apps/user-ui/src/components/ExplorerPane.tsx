"use client";

import {
  useCallback,
  useEffect,
  useState,
  type DragEvent as ReactDragEvent,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useI18n } from "@/i18n";
import {
  addFavorite,
  downloadFolderExportContent,
  exportFolder,
  getApprovalConfig,
  getFolderExport,
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
  type FolderExportJob,
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

// Combined folder export (post-roadmap phase 28, ADR 0107) - polled until
// terminal, same interval order of magnitude as the folder-export-job
// backoff base (document-service `folder_export_poll_interval_seconds`).
const FOLDER_EXPORT_POLL_MS = 3000;

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function loadViewMode(): ExplorerViewMode {
  if (typeof window === "undefined") return "list";
  return window.localStorage.getItem(VIEW_MODE_KEY) === "tree" ? "tree" : "list";
}

// Standalone panel within the dockable workspace (concept 8, since P16-S1) -
// Windows-Explorer-style folder navigation with folder CRUD (previously
// there was only navigation, no create/rename/delete in the UI - the
// backend endpoints already existed since P3-S3). Until P16-S1 it also
// carried the tab bar for open documents - since P16-S1 that moves together
// with the preview into its own dockview group (`DockableDocumentArea`,
// "document tabs going forward above the preview instead of above the
// explorer", concept 8) and is now rendered by dockview's own tab strip
// instead of a hand-built tab bar here.
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
  onMoveFolder,
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
  onMoveFolder: (folderId: string, newParentId: string) => Promise<boolean>;
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
  // Bulk metadata editing (8, P14-S12) - a "kind:id" set, same key format as
  // `favoriteKeys` above. Only meaningful in the list view outside the trash
  // (see checkbox rendering below) - cleared on folder change/trash toggle,
  // so it never references objects that are no longer visible.
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  // Folder move via drag & drop (8, P23-S4). `draggedFolderId` remembers
  // the dragged folder (also needed for the drop handler itself -
  // `dataTransfer` is not readable in the `dragover` event for security
  // reasons, only in the `drop` event); `dragOverFolderId` only controls the
  // visual feedback for which row currently counts as the target.
  const [draggedFolderId, setDraggedFolderId] = useState<string | null>(null);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);
  // Combined folder export (post-roadmap phase 28, ADR 0107) - `folderName`
  // kept alongside the job purely for the status text/downloaded filename
  // (the job itself only carries `folder_id`).
  const [folderExportJob, setFolderExportJob] = useState<FolderExportJob | null>(null);
  const [folderExportName, setFolderExportName] = useState<string | null>(null);
  const [linkCopyMessage, setLinkCopyMessage] = useState<string | null>(null);

  useEffect(() => {
    setSelectedKeys(new Set());
  }, [currentFolderId, showTrash]);

  useEffect(() => {
    if (!folderExportJob || folderExportJob.status === "completed") return;
    if (folderExportJob.status === "failed_permanent") return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const fresh = await getFolderExport(token, folderExportJob.id);
        if (cancelled) return;
        setFolderExportJob(fresh);
        if (fresh.status === "completed") {
          const blob = await downloadFolderExportContent(token, fresh.id);
          if (!cancelled) triggerBrowserDownload(blob, `${folderExportName ?? "Ordner"}-export.pdf`);
        }
      } catch {
        // A single missed poll shouldn't abort the whole flow - the next
        // interval tick simply tries again.
      }
    }, FOLDER_EXPORT_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [folderExportJob, folderExportName, token]);

  function handleFolderDragStart(folderId: string) {
    setDraggedFolderId(folderId);
  }

  function handleFolderDragEnd() {
    setDraggedFolderId(null);
    setDragOverFolderId(null);
  }

  function handleFolderDragOver(event: ReactDragEvent, targetFolderId: string) {
    if (!draggedFolderId || draggedFolderId === targetFolderId) return;
    event.preventDefault();
    setDragOverFolderId(targetFolderId);
  }

  async function handleFolderDrop(event: ReactDragEvent, targetFolderId: string) {
    event.preventDefault();
    setDragOverFolderId(null);
    const folderId = draggedFolderId;
    setDraggedFolderId(null);
    if (!folderId || folderId === targetFolderId) return;
    await onMoveFolder(folderId, targetFolderId);
  }

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

  // Class icons before each folder name (2.2a/2.2b) as well as the list
  // itself for the folder class selection when creating (user feedback: until
  // now the folder class wasn't selectable at all when creating, even though
  // the backend has supported it since P3-S3) - loaded once, not a blocker
  // for the rest of the display if this fails (falls back to the generic
  // folder icon/no class selection, see the `folderIcon()` fallback).
  useEffect(() => {
    if (!token) return;
    listObjectTypes(token, "folder")
      .then((types) => {
        setFolderIconById(Object.fromEntries(types.map((ot) => [ot.id, ot.icon])));
        setFolderObjectTypes(types);
      })
      .catch(() => {});
  }, [token]);

  // Reference-number display before the file name (2.2/8, P5e-S3) - object
  // type override per document kind plus a global default, see
  // lib/kennzeichen.ts. Same "load once, fall back on failure" pattern as
  // above.
  useEffect(() => {
    if (!token) return;
    listObjectTypes(token, "document")
      .then((types) => setDocumentTypeById(Object.fromEntries(types.map((ot) => [ot.id, ot]))))
      .catch(() => {});
    getKennzeichenConfig(token)
      .then((config) => setKennzeichenShowByDefault(config.show_before_filename))
      .catch(() => {});
  }, [token]);

  // Delete-request workflow (5.2, since P7-S1c): loaded once, whether the
  // regular trash action is configured to require approval via the
  // four-eyes principle - determines only the label ("delete" vs. "request
  // deletion"), the actual enforcement happens server-side in
  // document-service/folder-service independently of this. On failure (e.g.
  // permission-service unreachable) the safe default "false" remains - the
  // identical fallback principle as with the class icons above.
  useEffect(() => {
    if (!token) return;
    getApprovalConfig(token, "folder.delete")
      .then((config) => setFolderDeleteRequiresApproval(config.requires_approval))
      .catch(() => {});
    getApprovalConfig(token, "document.delete")
      .then((config) => setDocumentDeleteRequiresApproval(config.requires_approval))
      .catch(() => {});
  }, [token]);

  // Public share link (4.2a, P14-S10): the context menu entry is only
  // offered if the feature is active installation-wide ("no menu item" per
  // the concept wording when disabled) - on failure the safe default "false"
  // remains, the same fallback principle as for the other configurations
  // loaded once above.
  useEffect(() => {
    if (!token) return;
    getShareLinkConfig(token)
      .then((config) => setShareLinkEnabled(config.enabled))
      .catch(() => {});
  }, [token]);

  // Favorites/bookmark list (quickly finding things again, since P7-S1d):
  // load all favorites of the current user once (both object types in a
  // single call, `favorite-service` doesn't filter by type by default), as a
  // "type:id" set for O(1) membership checks in the context menu/⭐ prefix.
  // Reloaded after every change instead of optimistically updated, the same
  // "server remains the source of truth" principle as with the trash.
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

  // Trash toggle (5.2, since P7-S1, extended to folders since
  // P7-S1b) - deliberately minimal: only the current folder, no separate
  // special-area navigation (see phase 15).
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

  async function handleExportFolder(folder: Folder) {
    setFolderExportName(folder.name);
    const job = await exportFolder(token, folder.id);
    setFolderExportJob(job);
  }

  // Authenticated direct links (post-roadmap phase 29, ADR 0109) - a stable
  // resource ID in the URL plus the normal session/permission-check path
  // (unlike the anonymous, single-document share link, ADR 0047), resolved
  // client-side by DocumentWorkspace.tsx on mount.
  async function handleCopyLink(kind: "document" | "folder", id: string) {
    const url = `${window.location.origin}/?${kind}=${encodeURIComponent(id)}`;
    try {
      await navigator.clipboard.writeText(url);
      setLinkCopyMessage(t("explorer.linkCopied"));
    } catch {
      setLinkCopyMessage(t("explorer.linkCopyError"));
    }
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
        {
          label: t("explorer.exportFolder", { name: folder.name }),
          onSelect: () => handleExportFolder(folder),
        },
        {
          label: t("explorer.copyLink", { name: folder.name }),
          onSelect: () => handleCopyLink("folder", folder.id),
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
        {
          label: t("explorer.copyLink", { name }),
          onSelect: () => handleCopyLink("document", doc.id),
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

      {linkCopyMessage && <p className="hint">{linkCopyMessage}</p>}

      {folderExportJob && folderExportJob.status !== "completed" && (
        <p className="hint" role="status">
          {folderExportJob.status === "failed_permanent"
            ? t("explorer.exportFolderFailed", {
                name: folderExportName ?? "",
                error: folderExportJob.error_message ?? "",
              })
            : t("explorer.exportFolderInProgress", { name: folderExportName ?? "" })}
        </p>
      )}

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
          onMoveFolder={onMoveFolder}
          favoriteKeys={favoriteKeys}
          onFolderContextMenu={openFolderContextMenu}
          onDocumentContextMenu={openDocumentContextMenu}
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
                className={
                  "entry-row" + (dragOverFolderId === folder.id ? " entry-row-drag-over" : "")
                }
                key={folder.id}
                draggable
                onContextMenu={(e) => openFolderContextMenu(e, folder)}
                onDragStart={() => handleFolderDragStart(folder.id)}
                onDragEnd={handleFolderDragEnd}
                onDragOver={(e) => handleFolderDragOver(e, folder.id)}
                onDragLeave={() => setDragOverFolderId((prev) => (prev === folder.id ? null : prev))}
                onDrop={(e) => handleFolderDrop(e, folder.id)}
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
