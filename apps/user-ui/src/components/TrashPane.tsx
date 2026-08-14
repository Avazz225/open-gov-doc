"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listDeletedDocumentsGlobal,
  listDeletedFoldersGlobal,
  purgeDocument,
  purgeFolder,
  restoreDocument,
  restoreFolder,
  type DocumentSummary,
  type DocumentTrashScope,
  type Folder,
} from "@/lib/api";

// Trash family (2.5, P15-S1) - installation-wide special-area view,
// deliberately separate from the existing folder-scoped trash toggle in
// ExplorerPane.tsx (which continues to show only what is deleted WITHIN THE
// CURRENTLY OPEN folder - unchanged behavior, no backward-compatibility
// concern). Three structurally separate views, depending on the role of the
// logged-in user (same client-side gating pattern as MetadataPanel.tsx's
// reference-number admin check - an installation with differently configured
// role names would need to adjust these constants accordingly):
//   - "personal" (always available): only the user's own deletion markers, no
//     "delete permanently" - per the concept, regular users can only mark for
//     deletion.
//   - "admin" (only with trash_hard_delete_admin_role): full but
//     non-classified trash, including "delete permanently".
//   - "admin_classified" (only with classified_trash_hard_delete_admin_role):
//     structurally separate classified-documents trash (documents only,
//     per the concept folders have no classification) including "delete
//     permanently".
// Deliberately no "open" button: `folder-service`'s regular `GET /folders/
// {id}` treats a folder that is already in the trash as non-existent (see
// `repository.get_folder`) - an in-place preview of a deleted object would be
// a separate feature, not part of this session (the name/attributes shown in
// the list already satisfy the concept goal of "searchable/viewable").
const TRASH_HARD_DELETE_ADMIN_ROLE = "dms-admin";
const CLASSIFIED_TRASH_HARD_DELETE_ADMIN_ROLE = "dms-admin";

type PaneScope = "personal" | "admin" | "admin_classified";

export function TrashPane({ token }: { token: string }) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isTrashAdmin = Boolean(user?.realm_roles.includes(TRASH_HARD_DELETE_ADMIN_ROLE));
  const isClassifiedTrashAdmin = Boolean(
    user?.realm_roles.includes(CLASSIFIED_TRASH_HARD_DELETE_ADMIN_ROLE)
  );

  const [scope, setScope] = useState<PaneScope>("personal");
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const documentScope: DocumentTrashScope = scope;
      const [loadedDocuments, loadedFolders] = await Promise.all([
        listDeletedDocumentsGlobal(token, documentScope),
        // Per the concept, the classified-documents trash only knows documents.
        scope === "admin_classified" ? Promise.resolve([]) : listDeletedFoldersGlobal(token, scope),
      ]);
      setDocuments(loadedDocuments);
      setFolders(loadedFolders);
    } catch {
      setError(t("trash.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, scope, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleRestoreDocument(document: DocumentSummary) {
    setBusyId(document.id);
    setError(null);
    try {
      await restoreDocument(token, document.id);
      await reload();
    } catch {
      setError(t("trash.restoreError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleRestoreFolder(folder: Folder) {
    setBusyId(folder.id);
    setError(null);
    try {
      await restoreFolder(token, folder.id);
      await reload();
    } catch {
      setError(t("trash.restoreError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handlePurgeDocument(document: DocumentSummary) {
    if (!window.confirm(t("trash.purgeConfirm", { name: document.title }))) return;
    setBusyId(document.id);
    setError(null);
    try {
      await purgeDocument(token, document.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("trash.purgeError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handlePurgeFolder(folder: Folder) {
    if (!window.confirm(t("trash.purgeConfirm", { name: folder.name }))) return;
    setBusyId(folder.id);
    setError(null);
    try {
      await purgeFolder(token, folder.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("trash.purgeError"));
    } finally {
      setBusyId(null);
    }
  }

  const canPurge = scope !== "personal";
  const isEmpty = documents.length === 0 && folders.length === 0;

  return (
    <section className="trash-pane" aria-label={t("trash.paneLabel")}>
      <h2 className="pane-heading">{t("trash.heading")}</h2>
      <p className="hint">{t("trash.hint")}</p>

      <span className="view-mode-toggle" role="group" aria-label={t("trash.scopeLabel")}>
        <button
          type="button"
          className={scope === "personal" ? "view-mode-active" : undefined}
          aria-pressed={scope === "personal"}
          onClick={() => setScope("personal")}
        >
          {t("trash.scopePersonal")}
        </button>
        {isTrashAdmin && (
          <button
            type="button"
            className={scope === "admin" ? "view-mode-active" : undefined}
            aria-pressed={scope === "admin"}
            onClick={() => setScope("admin")}
          >
            {t("trash.scopeAdmin")}
          </button>
        )}
        {isClassifiedTrashAdmin && (
          <button
            type="button"
            className={scope === "admin_classified" ? "view-mode-active" : undefined}
            aria-pressed={scope === "admin_classified"}
            onClick={() => setScope("admin_classified")}
          >
            {t("trash.scopeAdminClassified")}
          </button>
        )}
      </span>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : isEmpty ? (
        <p className="empty-state">{t("trash.empty")}</p>
      ) : (
        <ul className="entry-list">
          {folders.map((folder) => (
            <li className="entry-row" key={`folder:${folder.id}`}>
              <span className="entry-name">
                {t("trash.typeFolder")} {folder.name}
              </span>
              <span className="actions">
                <button
                  type="button"
                  onClick={() => handleRestoreFolder(folder)}
                  disabled={busyId === folder.id}
                >
                  {t("trash.restore")}
                </button>
                {canPurge && (
                  <button
                    type="button"
                    onClick={() => handlePurgeFolder(folder)}
                    disabled={busyId === folder.id}
                  >
                    {t("trash.purge")}
                  </button>
                )}
              </span>
            </li>
          ))}
          {documents.map((document) => (
            <li className="entry-row" key={`document:${document.id}`}>
              <span className="entry-name">
                {t("trash.typeDocument")} {document.title}
              </span>
              <span className="actions">
                <button
                  type="button"
                  onClick={() => handleRestoreDocument(document)}
                  disabled={busyId === document.id}
                >
                  {t("trash.restore")}
                </button>
                {canPurge && (
                  <button
                    type="button"
                    onClick={() => handlePurgeDocument(document)}
                    disabled={busyId === document.id}
                  >
                    {t("trash.purge")}
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
