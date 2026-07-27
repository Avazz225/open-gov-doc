"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import type { DocumentSummary, Folder } from "@/lib/api";
import { UploadForm } from "./UploadForm";

interface BreadcrumbEntry {
  id: string;
  name: string;
}

// Obere linke Spalte des 3-Spalten-Layouts (Nutzer-Feedback nach P4-S3, 8):
// Windows-Explorer-artige Ordnernavigation mit Ordner-CRUD (bisher gab es
// nur Navigation, keine Erstellung/Umbenennung/Löschung in der UI - die
// Backend-Endpunkte existierten bereits seit P3-S3) plus einer Tableiste
// für geöffnete Dokumente. Die Tab-Auswahl treibt Metadaten-Panel und
// Vorschau (siehe `DocumentWorkspace`) synchron.
export function ExplorerPane({
  trail,
  folders,
  documents,
  isLoading,
  error,
  tabs,
  activeTabId,
  onOpenFolder,
  onBreadcrumbClick,
  onOpenDocument,
  onSelectTab,
  onCloseTab,
  onCreateFolder,
  onRenameFolder,
  onDeleteFolder,
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
  tabs: DocumentSummary[];
  activeTabId: string | null;
  onOpenFolder: (folder: Folder) => void;
  onBreadcrumbClick: (index: number) => void;
  onOpenDocument: (doc: DocumentSummary) => void;
  onSelectTab: (id: string) => void;
  onCloseTab: (id: string) => void;
  onCreateFolder: (name: string) => Promise<boolean>;
  onRenameFolder: (folderId: string, name: string) => Promise<boolean>;
  onDeleteFolder: (folderId: string) => Promise<boolean>;
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

  async function handleCreateSubmit(event: FormEvent) {
    event.preventDefault();
    if (!newFolderName.trim()) return;
    const ok = await onCreateFolder(newFolderName.trim());
    if (ok) {
      setNewFolderName("");
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
    if (!window.confirm(t("explorer.confirmDeleteFolder", { name: folder.name }))) return;
    await onDeleteFolder(folder.id);
  }

  return (
    <section className="explorer-pane" aria-label={t("explorer.paneLabel")}>
      {tabs.length > 0 && (
        <div className="tab-bar" role="tablist" aria-label={t("explorer.openDocuments")}>
          {tabs.map((tab) => (
            <div key={tab.id} className={`tab ${tab.id === activeTabId ? "tab-active" : ""}`}>
              <button
                type="button"
                role="tab"
                aria-selected={tab.id === activeTabId}
                onClick={() => onSelectTab(tab.id)}
              >
                {tab.title}
              </button>
              <button
                type="button"
                className="tab-close"
                aria-label={t("explorer.closeTab", { title: tab.title })}
                onClick={() => onCloseTab(tab.id)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

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

      <div className="explorer-toolbar">
        <button type="button" onClick={() => setIsCreatingFolder((v) => !v)}>
          {t("explorer.newFolder")}
        </button>
        <button type="button" onClick={() => setShowUpload((v) => !v)}>
          {t("explorer.toggleUpload")}
        </button>
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
        />
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : (
        <>
          {folders.length === 0 && documents.length === 0 && (
            <p className="empty-state">{t("folderBrowser.emptyFolder")}</p>
          )}
          <ul className="entry-list">
            {folders.map((folder) => (
              <li className="entry-row" key={folder.id}>
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
                      📁 {folder.name}
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
                        aria-label={t("explorer.deleteFolder", { name: folder.name })}
                        onClick={() => handleDelete(folder)}
                      >
                        🗑
                      </button>
                    </span>
                  </>
                )}
              </li>
            ))}
            {documents.map((doc) => (
              <li className="entry-row" key={doc.id}>
                <button
                  type="button"
                  className="entry-name"
                  onClick={() => onOpenDocument(doc)}
                >
                  📄 {doc.title}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
