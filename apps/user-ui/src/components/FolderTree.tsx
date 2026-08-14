"use client";

import { useEffect, useState, type DragEvent as ReactDragEvent } from "react";
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

// Baumansicht (2.2a/8, seit P5b-S4) - Alternative zur Listenansicht in
// ExplorerPane, baut die Ordnerhierarchie strukturell ab der Wurzel auf,
// Kinder werden erst beim Aufklappen nachgeladen (dasselbe Prinzip wie
// AdminSidebars gruppierte Navigation, hier aber rekursiv über beliebig
// viele Ebenen). Klick auf einen Ordnernamen navigiert dorthin - siehe
// ADR 0015 für die Begründung, warum der vollständige Breadcrumb-Pfad dabei
// clientseitig aus den bereits aufgeklappten Vorfahren rekonstruiert wird,
// statt einen neuen Backend-Endpunkt für den vollständigen Pfad zu bauen.
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
}) {
  const { t } = useI18n();
  const [childrenByParent, setChildrenByParent] = useState<Record<string, NodeChildren>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  // Ordner-Verschieben per Drag & Drop (8, P23-S4). `draggedFolderParentId`
  // wird beim Drag-Start mitgemerkt (nicht aus dem Baum ableitbar, da ein
  // Knoten hier keine Rückreferenz auf seinen Elternordner trägt) - nur so
  // lässt sich nach einem erfolgreichen Move gezielt der ALTE Elternordner
  // im Cache invalidieren, ohne den gesamten Baum neu zu laden.
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

  // Nach einem erfolgreichen Move: alter UND neuer Elternordner zeigen sonst
  // weiterhin ihren veralteten Kinderstand (Cache wurde beim ersten Aufklappen
  // gefüllt und danach nie wieder angefasst) - nur betroffene, bereits
  // geladene Knoten neu laden, kein kompletter Baum-Reset.
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

  // `targetPath` ist die Vorfahrenkette des Ziels INKLUSIVE des Ziels selbst
  // (siehe Aufrufstelle unten) - enthält sie die gezogene Ordner-ID, wäre das
  // Ziel entweder der Ordner selbst oder einer seiner (im Baum aktuell
  // sichtbaren) Nachfahren. Der Backend-Endpunkt selbst prüft nur "nicht sein
  // eigener Elternordner" (siehe folder-service `update_folder`), nicht
  // tiefere Zyklen - diese clientseitige Prüfung ist eine zusätzliche
  // Sicherung für den im Baum sichtbaren Teil, kein Ersatz für eine
  // vollständige Server-seitige Zyklusprüfung (siehe "Offene Punkte").
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
    // Nur beim Wechsel des Tokens neu laden - `ensureLoaded` selbst hängt von
    // Komponentenzustand ab, der sich bei jedem Aufruf ändert.
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
            <span className="tree-row">
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
