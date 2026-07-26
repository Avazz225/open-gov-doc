"use client";

import { useCallback, useEffect, useState } from "react";
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

const ROOT_FOLDER: Pick<Folder, "id" | "name"> = { id: "root", name: "Start" };

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
  const [trail, setTrail] = useState<Array<Pick<Folder, "id" | "name">>>([ROOT_FOLDER]);
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
        setError(err instanceof ApiError ? err.message : "Laden fehlgeschlagen");
      } finally {
        setIsLoading(false);
      }
    },
    [accessToken]
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
      setError(err instanceof ApiError ? err.message : "Download fehlgeschlagen");
    }
  }

  return (
    <main className="page">
      <div className="top-bar">
        <h1>Dokumente</h1>
        <div>
          {user && <span>{user.username} </span>}
          <button type="button" onClick={logout}>
            Abmelden
          </button>
        </div>
      </div>

      <nav className="breadcrumbs" aria-label="Ordnerpfad">
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
        <p>Lade...</p>
      ) : (
        <>
          {folders.length === 0 && documents.length === 0 && (
            <p className="empty-state">Dieser Ordner ist leer.</p>
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
                    Herunterladen
                  </button>
                  <button type="button" onClick={() => setPreviewTarget(doc)}>
                    Vorschau
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
