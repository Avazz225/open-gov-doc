"use client";

import {
  useEffect,
  useState,
  type DragEvent as ReactDragEvent,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listChildFolders,
  listDocumentsInFolder,
  type DocumentSummary,
  type Folder,
  type ObjectType,
} from "@/lib/api";
import { folderIcon } from "@/lib/icons";
import { formatDocumentTitle } from "@/lib/kennzeichen";

interface NodeChildren {
  folders: Folder[];
  documents: DocumentSummary[];
}

// Tree view (2.2a/8, since P5b-S4) - an alternative to the list view in
// ExplorerPane, builds up the folder hierarchy structurally starting from
// the root, children are only loaded on expand (the same principle as
// AdminSidebar's grouped navigation, but recursive here across arbitrarily
// many levels). Clicking a folder name navigates there - see
// ADR 0015 for the rationale behind reconstructing the full breadcrumb path
// client-side from the already-expanded ancestors, instead of building a
// new backend endpoint for the full path.
export function FolderTree({
  token,
  rootLabel,
  folderIcons,
  documentTypeById,
  kennzeichenShowByDefault,
  favoriteKeys,
  onOpenDocument,
  onNavigateToFolder,
  onMoveFolder,
  onFolderContextMenu,
  onDocumentContextMenu,
}: {
  token: string;
  rootLabel: string;
  folderIcons: Record<number, string | null>;
  documentTypeById: Record<number, ObjectType>;
  kennzeichenShowByDefault: boolean;
  favoriteKeys: Set<string>;
  onOpenDocument: (doc: DocumentSummary) => void;
  onNavigateToFolder: (path: Folder[]) => void;
  onMoveFolder: (folderId: string, newParentId: string) => Promise<boolean>;
  // Right-click context menu (P23-S8) - builds on the logic already
  // existing in ExplorerPane (delete/favorite/share link), the
  // `ContextMenu` instance there + its `contextMenu` state are reused
  // (click coordinates are screen-relative, regardless of which nested
  // component triggers the call) - no second menu, no logic duplication
  // for delete approval/favorite status/share link here.
  onFolderContextMenu: (event: ReactMouseEvent, folder: Folder) => void;
  onDocumentContextMenu: (event: ReactMouseEvent, doc: DocumentSummary) => void;
}) {
  const { t } = useI18n();
  const [childrenByParent, setChildrenByParent] = useState<Record<string, NodeChildren>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Folder move via drag & drop (8, P23-S4). `draggedFolderParentId`
  // is recorded at drag start (not derivable from the tree, since a node
  // here carries no back-reference to its parent folder) - this is the only
  // way to selectively invalidate the OLD parent folder in the cache after a
  // successful move, without reloading the entire tree.
  const [draggedFolderId, setDraggedFolderId] = useState<string | null>(null);
  const [draggedFolderParentId, setDraggedFolderParentId] = useState<string | null>(null);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);

  async function fetchChildren(folderId: string) {
    setLoadingIds((prev) => new Set(prev).add(folderId));
    try {
      const [folders, documents] = await Promise.all([
        listChildFolders(token, folderId),
        listDocumentsInFolder(token, folderId),
      ]);
      setChildrenByParent((prev) => ({ ...prev, [folderId]: { folders, documents } }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("explorer.treeLoadError"));
    } finally {
      setLoadingIds((prev) => {
        const next = new Set(prev);
        next.delete(folderId);
        return next;
      });
    }
  }

  async function ensureLoaded(folderId: string) {
    if (childrenByParent[folderId] || loadingIds.has(folderId)) return;
    await fetchChildren(folderId);
  }

  // After a successful move: otherwise both the old AND new parent folder
  // would keep showing their stale children (the cache was filled on first
  // expand and never touched again afterward) - only reload the affected,
  // already-loaded nodes, no complete tree reset.
  async function refreshIfLoaded(folderId: string) {
    if (childrenByParent[folderId]) await fetchChildren(folderId);
  }

  function handleDragStart(folderId: string, parentId: string) {
    setDraggedFolderId(folderId);
    setDraggedFolderParentId(parentId);
  }

  function handleDragEnd() {
    setDraggedFolderId(null);
    setDraggedFolderParentId(null);
    setDragOverFolderId(null);
  }

  // `targetPath` is the ancestor chain of the target INCLUDING the target
  // itself (see call site below) - if it contains the dragged folder's ID,
  // the target would either be the folder itself or one of its (currently
  // visible in the tree) descendants. The backend endpoint itself only
  // checks "not its own parent folder" (see folder-service `update_folder`),
  // not deeper cycles - this client-side check is an additional safeguard
  // for the part visible in the tree, not a replacement for a complete
  // server-side cycle check (see "Open Points").
  function isValidDropTarget(targetFolderId: string, targetPath: Folder[]): boolean {
    if (!draggedFolderId) return false;
    return !targetPath.some((f) => f.id === draggedFolderId);
  }

  function handleDragOver(event: ReactDragEvent, targetFolderId: string, targetPath: Folder[]) {
    if (!isValidDropTarget(targetFolderId, targetPath)) return;
    event.preventDefault();
    setDragOverFolderId(targetFolderId);
  }

  async function handleDrop(event: ReactDragEvent, targetFolderId: string, targetPath: Folder[]) {
    event.preventDefault();
    setDragOverFolderId(null);
    const folderId = draggedFolderId;
    const parentId = draggedFolderParentId;
    setDraggedFolderId(null);
    setDraggedFolderParentId(null);
    if (!folderId || !isValidDropTarget(targetFolderId, targetPath)) return;
    const ok = await onMoveFolder(folderId, targetFolderId);
    if (ok) {
      if (parentId) await refreshIfLoaded(parentId);
      await refreshIfLoaded(targetFolderId);
    }
  }

  useEffect(() => {
    if (token) ensureLoaded("root");
    // Only reload when the token changes - `ensureLoaded` itself depends on
    // component state that changes on every call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function toggleExpand(folderId: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
    ensureLoaded(folderId);
  }

  function selectFolder(path: Folder[]) {
    const folderId = path.length > 0 ? path[path.length - 1].id : "root";
    onNavigateToFolder(path);
    setExpanded((prev) => new Set(prev).add(folderId));
    ensureLoaded(folderId);
  }

  function renderChildren(folderId: string, path: Folder[]) {
    const node = childrenByParent[folderId];
    if (!node) {
      return loadingIds.has(folderId) ? (
        <ul className="tree-children">
          <li className="empty-state">{t("common.loading")}</li>
        </ul>
      ) : null;
    }
    if (node.folders.length === 0 && node.documents.length === 0) {
      return (
        <ul className="tree-children">
          <li className="empty-state">{t("folderBrowser.emptyFolder")}</li>
        </ul>
      );
    }
    return (
      <ul className="tree-children">
        {node.folders.map((folder) => {
          const childPath = [...path, folder];
          const isExpanded = expanded.has(folder.id);
          const icon = folderIcon(
            folder.object_type_id !== null ? folderIcons[folder.object_type_id] : null
          );
          return (
            <li key={folder.id}>
              <span
                className={
                  "tree-row" + (dragOverFolderId === folder.id ? " tree-row-drag-over" : "")
                }
                draggable
                onDragStart={() => handleDragStart(folder.id, folderId)}
                onDragEnd={handleDragEnd}
                onDragOver={(e) => handleDragOver(e, folder.id, childPath)}
                onDragLeave={() => setDragOverFolderId((prev) => (prev === folder.id ? null : prev))}
                onDrop={(e) => handleDrop(e, folder.id, childPath)}
                onContextMenu={(e) => onFolderContextMenu(e, folder)}
              >
                <button
                  type="button"
                  className="tree-toggle"
                  aria-label={
                    isExpanded
                      ? t("explorer.collapseFolder", { name: folder.name })
                      : t("explorer.expandFolder", { name: folder.name })
                  }
                  onClick={() => toggleExpand(folder.id)}
                >
                  {isExpanded ? "▾" : "▸"}
                </button>
                <button type="button" className="entry-name" onClick={() => selectFolder(childPath)}>
                  {favoriteKeys.has(`folder:${folder.id}`) && "⭐ "}
                  {icon} {folder.name}
                </button>
              </span>
              {isExpanded && renderChildren(folder.id, childPath)}
            </li>
          );
        })}
        {node.documents.map((doc) => (
          <li key={doc.id}>
            <span className="tree-row" onContextMenu={(e) => onDocumentContextMenu(e, doc)}>
              <span className="tree-toggle-spacer" aria-hidden="true" />
              <button type="button" className="entry-name" onClick={() => onOpenDocument(doc)}>
                {favoriteKeys.has(`document:${doc.id}`) && "⭐ "}
                📄 {formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault)}
              </button>
            </span>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <div className="folder-tree" aria-label={t("explorer.treeLabel")}>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <span
        className={"tree-row" + (dragOverFolderId === "root" ? " tree-row-drag-over" : "")}
        onDragOver={(e) => handleDragOver(e, "root", [])}
        onDragLeave={() => setDragOverFolderId((prev) => (prev === "root" ? null : prev))}
        onDrop={(e) => handleDrop(e, "root", [])}
      >
        <span className="tree-toggle-spacer" aria-hidden="true" />
        <button type="button" className="entry-name" onClick={() => selectFolder([])}>
          📁 {rootLabel}
        </button>
      </span>
      {renderChildren("root", [])}
    </div>
  );
}
