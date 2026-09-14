"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getDocument,
  getFolder,
  listFolderReferences,
  searchDocuments,
  type DocumentSummary,
  type FolderReference,
  type SearchResult,
} from "@/lib/api";

// Installation-wide hand-folder/work-tray overview (14.2, Post-Roadmap
// Phase 35 Session 4, ADR 0146) - closes the gap ADR 0118's own
// "Consequences" named ("no cross-case index or search surfacing for hand
// folders... no new admin-ui/user-ui surface for browsing 'all hand
// folders'/'all work trays' installation-wide"). Two independent lists,
// same pane: hand-folder references (search-service's new cross-folder
// index, permission-filtered by each referenced folder's own `folder.read`)
// and unclaimed work-tray documents (the existing document index, filtered
// via the new `registered=false` param - work trays are deliberately not a
// new entity, ADR 0118, so this reuses the document search index rather
// than needing its own indexing).
export function HandFolderOverviewPane({
  token,
  onOpenDocument,
  onOpenFolder,
}: {
  token: string;
  onOpenDocument: (doc: DocumentSummary) => void;
  onOpenFolder: (folderId: string) => void;
}) {
  const { t } = useI18n();

  const [references, setReferences] = useState<FolderReference[]>([]);
  const [referencesLoading, setReferencesLoading] = useState(true);
  const [referencesError, setReferencesError] = useState<string | null>(null);

  const [workTrayDocs, setWorkTrayDocs] = useState<SearchResult[]>([]);
  const [workTrayLoading, setWorkTrayLoading] = useState(true);
  const [workTrayError, setWorkTrayError] = useState<string | null>(null);

  const reloadReferences = useCallback(async () => {
    if (!token) return;
    setReferencesLoading(true);
    setReferencesError(null);
    try {
      const response = await listFolderReferences(token, { limit: 50 });
      setReferences(response.results);
    } catch (err) {
      setReferencesError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setReferencesLoading(false);
    }
  }, [token, t]);

  const reloadWorkTray = useCallback(async () => {
    if (!token) return;
    setWorkTrayLoading(true);
    setWorkTrayError(null);
    try {
      const response = await searchDocuments(token, { registered: false, limit: 50 });
      setWorkTrayDocs(response.results);
    } catch (err) {
      setWorkTrayError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setWorkTrayLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reloadReferences();
    reloadWorkTray();
  }, [reloadReferences, reloadWorkTray]);

  async function handleOpenReferencedDocument(ref: FolderReference) {
    try {
      onOpenDocument(await getDocument(token, ref.document_id));
    } catch {
      // Deleted since the reference was added, or not readable - nothing
      // to open, same graceful handling as CasesPane's document links.
    }
  }

  async function handleOpenReferencedFolder(ref: FolderReference) {
    try {
      await getFolder(token, ref.folder_id);
      onOpenFolder(ref.folder_id);
    } catch {
      // Folder deleted/not readable since the reference was indexed.
    }
  }

  return (
    <section className="hand-folder-overview-pane" aria-label={t("handFolderOverview.paneLabel")}>
      <h2 className="pane-heading">{t("handFolderOverview.heading")}</h2>

      <section aria-labelledby="hand-folder-references-heading">
        <h3 id="hand-folder-references-heading">{t("handFolderOverview.referencesHeading")}</h3>
        <p className="hint">{t("handFolderOverview.referencesHint")}</p>
        {referencesError && (
          <p className="error-text" role="alert">
            {referencesError}
          </p>
        )}
        {referencesLoading ? (
          <p>{t("common.loading")}</p>
        ) : references.length === 0 ? (
          <p className="empty-state">{t("handFolderOverview.referencesEmpty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("handFolderOverview.folderColumn")}</th>
                <th>{t("handFolderOverview.documentColumn")}</th>
                <th>{t("handFolderOverview.addedByColumn")}</th>
                <th>{t("handFolderOverview.addedAtColumn")}</th>
              </tr>
            </thead>
            <tbody>
              {references.map((ref) => (
                <tr key={`${ref.folder_id}:${ref.document_id}`}>
                  <td>
                    <button type="button" onClick={() => handleOpenReferencedFolder(ref)}>
                      {ref.folder_name ?? ref.folder_id}
                    </button>
                  </td>
                  <td>
                    <button type="button" onClick={() => handleOpenReferencedDocument(ref)}>
                      {ref.document_title ?? ref.document_id}
                    </button>
                  </td>
                  <td>{ref.added_by}</td>
                  <td>{new Date(ref.added_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-labelledby="work-tray-heading">
        <h3 id="work-tray-heading">{t("handFolderOverview.workTrayHeading")}</h3>
        <p className="hint">{t("handFolderOverview.workTrayHint")}</p>
        {workTrayError && (
          <p className="error-text" role="alert">
            {workTrayError}
          </p>
        )}
        {workTrayLoading ? (
          <p>{t("common.loading")}</p>
        ) : workTrayDocs.length === 0 ? (
          <p className="empty-state">{t("handFolderOverview.workTrayEmpty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("handFolderOverview.documentColumn")}</th>
                <th>{t("handFolderOverview.folderColumn")}</th>
                <th>{t("handFolderOverview.createdByColumn")}</th>
              </tr>
            </thead>
            <tbody>
              {workTrayDocs.map((doc) => (
                <tr key={doc.id}>
                  <td>
                    <button type="button" onClick={() => onOpenDocument(doc)}>
                      {doc.title}
                    </button>
                  </td>
                  <td>{doc.folder_name ?? "-"}</td>
                  <td>{doc.created_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </section>
  );
}
