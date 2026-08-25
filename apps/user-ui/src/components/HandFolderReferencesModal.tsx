"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  addFolderDocumentReference,
  getDocument,
  listFolderDocumentReferences,
  removeFolderDocumentReference,
  type DocumentSummary,
  type Folder,
  type FolderDocumentReference,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface ResolvedReference {
  reference: FolderDocumentReference;
  document: DocumentSummary | null;
}

// Hand folders (14.2, post-roadmap phase 31 session 7, ADR 0118) - same
// modal pattern as ShareLinkModal.tsx/FolderRetentionModal.tsx. Gated
// server-side by `folder.write`/`folder.read` (folder-scoped, not root) -
// a 403 here means the viewer genuinely lacks access to this specific
// folder's compilation, shown as a plain error rather than hidden, since
// the modal is only reachable at all via an already-visible folder.
//
// The reference row itself only carries `document_id` plus a live-resolved
// `current_version_number`/`document_deleted_at` (folder-service has no
// notion of a document's title) - resolved further here via the existing
// `getDocument()` call, same "resolve display name live, tolerate a 404"
// pattern as FavoritesPane.tsx.
export function HandFolderReferencesModal({
  folder,
  onClose,
}: {
  folder: Folder;
  onClose: () => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [entries, setEntries] = useState<ResolvedReference[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newDocumentId, setNewDocumentId] = useState("");
  const [isAdding, setIsAdding] = useState(false);
  const [busyDocumentId, setBusyDocumentId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const references = await listFolderDocumentReferences(accessToken, folder.id);
      const resolved = await Promise.all(
        references.map(async (reference): Promise<ResolvedReference> => {
          const document = await getDocument(accessToken, reference.document_id).catch(
            () => null
          );
          return { reference, document };
        })
      );
      setEntries(resolved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("handFolder.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, folder.id, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleAdd() {
    if (!accessToken || !user || !newDocumentId.trim()) return;
    setIsAdding(true);
    setError(null);
    try {
      await addFolderDocumentReference(accessToken, folder.id, {
        documentId: newDocumentId.trim(),
        addedBy: user.username,
      });
      setNewDocumentId("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("handFolder.addError"));
    } finally {
      setIsAdding(false);
    }
  }

  async function handleRemove(reference: FolderDocumentReference) {
    if (!accessToken || !user) return;
    setBusyDocumentId(reference.document_id);
    setError(null);
    try {
      await removeFolderDocumentReference(
        accessToken,
        folder.id,
        reference.document_id,
        user.username
      );
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("handFolder.removeError"));
    } finally {
      setBusyDocumentId(null);
    }
  }

  const active = entries.filter(({ reference }) => reference.removed_at === null);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={t("handFolder.heading", { name: folder.name })}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="pane-heading">{t("handFolder.heading", { name: folder.name })}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>
        <p className="hint">{t("handFolder.description")}</p>

        <div className="inline-form">
          <label>
            {t("handFolder.documentIdLabel")}
            <input value={newDocumentId} onChange={(e) => setNewDocumentId(e.target.value)} />
          </label>
          <button type="button" onClick={handleAdd} disabled={isAdding || !newDocumentId.trim()}>
            {isAdding ? t("handFolder.adding") : t("handFolder.add")}
          </button>
        </div>

        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : active.length === 0 ? (
          <p className="empty-state">{t("handFolder.empty")}</p>
        ) : (
          <ul className="entry-list">
            {active.map(({ reference, document }) => (
              <li className="entry-row" key={reference.document_id}>
                <span className="entry-name">
                  {document?.title ?? t("handFolder.unresolvedTitle", { id: reference.document_id })}
                  {reference.document_deleted_at ? ` (${t("handFolder.deletedHint")})` : ""}
                </span>
                <span className="actions">
                  <button
                    type="button"
                    onClick={() => handleRemove(reference)}
                    disabled={busyDocumentId === reference.document_id}
                  >
                    {t("handFolder.remove")}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
