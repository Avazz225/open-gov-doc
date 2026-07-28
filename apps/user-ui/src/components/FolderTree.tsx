"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listChildFolders,
  listDocumentsInFolder,
  type DocumentSummary,
  type Folder,
} from "@/lib/api";
import { folderIcon } from "@/lib/icons";

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
  onOpenDocument,
  onNavigateToFolder,
}: {
  token: string;
  rootLabel: string;
  folderIcons: Record<number, string | null>;
  onOpenDocument: (doc: DocumentSummary) => void;
  onNavigateToFolder: (path: Folder[]) => void;
}) {
  const { t } = useI18n();
  const [childrenByParent, setChildrenByParent] = useState<Record<string, NodeChildren>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set(["root"]));
  const [loadingIds, setLoadingIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  async function ensureLoaded(folderId: string) {
    if (childrenByParent[folderId] || loadingIds.has(folderId)) return;
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
              <span className="tree-row">
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
                📄 {doc.title}
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
      <span className="tree-row">
        <span className="tree-toggle-spacer" aria-hidden="true" />
        <button type="button" className="entry-name" onClick={() => selectFolder([])}>
          📁 {rootLabel}
        </button>
      </span>
      {renderChildren("root", [])}
    </div>
  );
}
