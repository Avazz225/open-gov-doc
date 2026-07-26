"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type Folder,
  downloadDocument,
  listChildFolders,
  listDocumentsInFolder,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PreviewStub } from "./PreviewStub";
import { UploadForm } from "./UploadForm";

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

export function FolderBrowser() {
  const { user, accessToken, logout } = useAuth();
  const { t } = useI18n();
  const [trail, setTrail] = useState<Array<Pick<Folder, "id" | "name">>>(() => [
    { id: "root", name: t("folderBrowser.rootLabel") },
  ]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [previewTarget, setPreviewTarget] = useState<DocumentSummary | null>(null);

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

  function goToBreadcrumb(index: number) {
    setTrail((prev) => prev.slice(0, index + 1));
  }

  async function handleDownload(doc: DocumentSummary) {
    if (!accessToken) return;
    try {
      const blob = await downloadDocument(accessToken, doc.id);
      triggerBrowserDownload(blob, doc.title);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("folderBrowser.downloadError"));
    }
  }

  return (
    <main className="page">
      <div className="top-bar">
        <h1>{t("folderBrowser.title")}</h1>
        <div>
          {user && <span>{user.username} </span>}
          <button type="button" onClick={logout}>
            {t("common.logout")}
          </button>
        </div>
      </div>

      <nav className="breadcrumbs" aria-label={t("folderBrowser.breadcrumbLabel")}>
        {trail.map((entry, index) => (
          <span key={entry.id}>
            {index > 0 && <span className="separator"> / </span>}
            <button type="button" onClick={() => goToBreadcrumb(index)}>
              {entry.name}
            </button>
          </span>
        ))}
      </nav>

      {accessToken && user && (
        <UploadForm
          token={accessToken}
          folderId={currentFolder.id}
          createdBy={user.username}
          onUploaded={() => load(currentFolder.id)}
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
                <button
                  type="button"
                  className="entry-name"
                  onClick={() => openFolder(folder)}
                >
                  📁 {folder.name}
                </button>
              </li>
            ))}
            {documents.map((doc) => (
              <li className="entry-row" key={doc.id}>
                <span>📄 {doc.title}</span>
                <span className="actions">
                  <button type="button" onClick={() => handleDownload(doc)}>
                    {t("folderBrowser.download")}
                  </button>
                  <button type="button" onClick={() => setPreviewTarget(doc)}>
                    {t("folderBrowser.preview")}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {previewTarget && (
        <PreviewStub title={previewTarget.title} onClose={() => setPreviewTarget(null)} />
      )}
    </main>
  );
}
