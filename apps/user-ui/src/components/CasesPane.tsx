"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  addFavorite,
  ApiError,
  exportCaseXdomea,
  exportCaseXjustiz,
  getCase,
  getDocument,
  importXdomeaIntoCase,
  importXjustizIntoCase,
  listCaseDocuments,
  listCases,
  listFavorites,
  listProcessDefinitions,
  removeFavorite,
  type Case,
  type CaseDocumentReference,
  type DocumentSummary,
  type ProcessDefinition,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Minimal case-browsing UI (14.2, post-roadmap phase 34 session 3, ADR
// 0141) - the first place in `user-ui` a `case-service` Case can be browsed
// at all (previously API-only from this app's perspective). List view here,
// detail view in `CaseDetail` below (this app's first genuine list->detail
// drill-down - every other pane is a flat list with inline row actions, see
// ADR 0141 "Rationale" for why this one is different). Wires all four
// case-level XDOMEA/XJustiz export/import actions (ADR 0126/0128/0129/0139)
// onto the detail view.
function formatDate(value: string | null, locale: string): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(locale);
}

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function CasesPane({
  token,
  onOpenDocument,
  openCaseId,
}: {
  token: string;
  onOpenDocument: (doc: DocumentSummary) => void;
  // "Open" from the favorites bookmark list (Phase 45 Session 2) - a
  // `useEffect` re-selects whenever this prop changes, rather than only
  // reading it as a `useState` initial value, since a future refactor of
  // DocumentWorkspace.tsx's view-switching (currently a ternary that remounts
  // this pane on every switch, unlike the always-mounted documents area)
  // could otherwise silently stop picking up a new id.
  openCaseId?: string | null;
}) {
  const { t, locale } = useI18n();
  const { permissions, user } = useAuth();
  const username = user?.username ?? "";
  // Export/import are archival actions (ADR 0126/0128/0129/0139), gated
  // server-side on `archival.write` - hidden client-side too so a principal
  // without it doesn't see buttons that would just 403, same idiom as
  // `RecordsQuarantinePanel`'s `admin.records_quarantine` check. Browsing
  // itself stays ungated, matching `case.read`'s "everyone" default
  // (ADR 0070) - this pane's own list/detail view is not an archival
  // action.
  const canArchive = permissions.includes("archival.write");

  const [cases, setCases] = useState<Case[]>([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  // Case-binder favorites (Phase 45 Session 2) - deliberately deferred at
  // plan approval (P7-S1d) until this pane existed at all. Same
  // "server remains source of truth, reload after every toggle" idiom as
  // `ExplorerPane.tsx`'s document/folder favorites, scoped to
  // `object_type="case"` only (a plain `Set<caseId>`, no compound key
  // needed here since there's only one object type in this pane).
  const [favoriteCaseIds, setFavoriteCaseIds] = useState<Set<string>>(new Set());
  const [favoriteError, setFavoriteError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setCases(await listCases(token, statusFilter || undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, statusFilter, t]);

  const reloadFavorites = useCallback(async () => {
    if (!token || !username) return;
    try {
      const favorites = await listFavorites(token, username, "case");
      setFavoriteCaseIds(new Set(favorites.map((f) => f.object_id)));
    } catch {
      // Non-critical - the pane still works without the favorite markers.
    }
  }, [token, username]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    reloadFavorites();
  }, [reloadFavorites]);

  useEffect(() => {
    if (openCaseId) setSelectedCaseId(openCaseId);
  }, [openCaseId]);

  async function toggleFavoriteCase(caseId: string) {
    if (!token || !username) return;
    setFavoriteError(null);
    try {
      if (favoriteCaseIds.has(caseId)) {
        await removeFavorite(token, { user_id: username, object_type: "case", object_id: caseId });
      } else {
        await addFavorite(token, { user_id: username, object_type: "case", object_id: caseId });
      }
      await reloadFavorites();
    } catch {
      setFavoriteError(t("cases.favoriteError"));
    }
  }

  if (selectedCaseId) {
    return (
      <CaseDetail
        token={token}
        caseId={selectedCaseId}
        canArchive={canArchive}
        onBack={() => setSelectedCaseId(null)}
        onOpenDocument={onOpenDocument}
        isFavorite={favoriteCaseIds.has(selectedCaseId)}
        onToggleFavorite={() => toggleFavoriteCase(selectedCaseId)}
        favoriteError={favoriteError}
      />
    );
  }

  return (
    <section className="cases-pane" aria-label={t("cases.paneLabel")}>
      <h2 className="pane-heading">{t("cases.heading")}</h2>
      <p className="hint">{t("cases.hint")}</p>

      {favoriteError && (
        <p className="error-text" role="alert">
          {favoriteError}
        </p>
      )}

      <label>
        {t("cases.statusFilterLabel")}
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">{t("cases.statusAll")}</option>
          <option value="open">{t("cases.statusOpen")}</option>
          <option value="closed">{t("cases.statusClosed")}</option>
        </select>
      </label>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : cases.length === 0 ? (
        <p className="empty-state">{t("cases.empty")}</p>
      ) : (
        <ul className="entry-list">
          {cases.map((c) => (
            <li className="entry-row" key={c.id}>
              <button
                type="button"
                className="entry-name"
                onClick={() => setSelectedCaseId(c.id)}
              >
                {c.name}
                {c.vorgangsnummer ? ` (${c.vorgangsnummer})` : ""}
              </button>
              <span className="entry-meta">
                {c.status === "open" ? t("cases.statusOpen") : t("cases.statusClosed")}
                {" · "}
                {t("cases.createdAt", { date: formatDate(c.created_at, locale) })}
              </span>
              <button
                type="button"
                className="favorite-toggle"
                aria-label={
                  favoriteCaseIds.has(c.id)
                    ? t("cases.removeFavorite", { name: c.name })
                    : t("cases.addFavorite", { name: c.name })
                }
                onClick={() => toggleFavoriteCase(c.id)}
              >
                {favoriteCaseIds.has(c.id) ? "★" : "☆"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {canArchive && (
        <NewCaseImportSection
          token={token}
          onCaseCreated={(caseId) => {
            reload();
            setSelectedCaseId(caseId);
          }}
        />
      )}
    </section>
  );
}

// "Import creates a new case" (14.2, Post-Roadmap Phase 42 Session 1) -
// the one half of ADR 0128/0139's import shape ("attach to an EXISTING
// case" OR "start a brand-new case via a process definition") that had NO
// UI entry point at all before this session (ADR 0141 "Rationale":
// deliberately deferred, since no process-definition-picker UI component
// existed anywhere in the codebase yet). Lives on the case LIST view, not
// inside `CaseDetail` - unlike the case-detail import forms (always
// `case_id`), this one has no case to attach to yet by construction.
// Minimal picker: a plain `<select>` populated from `listProcessDefinitions`
// - the exact same pattern `office-addin`'s `WorkflowPanel` already
// established for "start a workflow", not a full picker UI.
function NewCaseImportSection({
  token,
  onCaseCreated,
}: {
  token: string;
  onCaseCreated: (caseId: string) => void;
}) {
  const { t } = useI18n();
  const [definitions, setDefinitions] = useState<ProcessDefinition[]>([]);

  const [xdomeaOpen, setXdomeaOpen] = useState(false);
  const [xdomeaFile, setXdomeaFile] = useState<File | null>(null);
  const [xdomeaFolderId, setXdomeaFolderId] = useState("root");
  const [xdomeaDefinitionId, setXdomeaDefinitionId] = useState("");
  const [xdomeaImporting, setXdomeaImporting] = useState(false);
  const [xdomeaError, setXdomeaError] = useState<string | null>(null);

  const [xjustizOpen, setXjustizOpen] = useState(false);
  const [xjustizFile, setXjustizFile] = useState<File | null>(null);
  const [xjustizFolderId, setXjustizFolderId] = useState("root");
  const [xjustizDefinitionId, setXjustizDefinitionId] = useState("");
  const [xjustizImporting, setXjustizImporting] = useState(false);
  const [xjustizError, setXjustizError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    listProcessDefinitions(token)
      .then(setDefinitions)
      .catch(() => setDefinitions([]));
  }, [token]);

  if (definitions.length === 0) return null;

  async function handleXdomeaImport() {
    if (!token || !xdomeaFile || !xdomeaFolderId.trim() || !xdomeaDefinitionId) return;
    setXdomeaError(null);
    setXdomeaImporting(true);
    try {
      const result = await importXdomeaIntoCase(token, {
        file: xdomeaFile,
        folderId: xdomeaFolderId.trim(),
        processDefinitionId: Number(xdomeaDefinitionId),
      });
      setXdomeaOpen(false);
      setXdomeaFile(null);
      setXdomeaDefinitionId("");
      if (result.case_id) onCaseCreated(result.case_id);
    } catch (err) {
      setXdomeaError(err instanceof ApiError ? err.message : t("cases.xdomeaImportErrorGeneric"));
    } finally {
      setXdomeaImporting(false);
    }
  }

  async function handleXjustizImport() {
    if (!token || !xjustizFile || !xjustizFolderId.trim() || !xjustizDefinitionId) return;
    setXjustizError(null);
    setXjustizImporting(true);
    try {
      const result = await importXjustizIntoCase(token, {
        file: xjustizFile,
        folderId: xjustizFolderId.trim(),
        processDefinitionId: Number(xjustizDefinitionId),
      });
      setXjustizOpen(false);
      setXjustizFile(null);
      setXjustizDefinitionId("");
      if (result.case_id) onCaseCreated(result.case_id);
    } catch (err) {
      setXjustizError(
        err instanceof ApiError ? err.message : t("cases.xjustizImportErrorGeneric")
      );
    } finally {
      setXjustizImporting(false);
    }
  }

  return (
    <>
      <h3>{t("cases.newCaseImportHeading")}</h3>
      <p className="hint">{t("cases.newCaseImportHint")}</p>

      <button type="button" onClick={() => setXdomeaOpen((prev) => !prev)}>
        {t("cases.xdomeaImport")}
      </button>
      {xdomeaOpen && (
        <div className="inline-form">
          <label>
            {t("cases.importFileLabel")}
            <input type="file" onChange={(e) => setXdomeaFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            {t("cases.importFolderLabel")}
            <input
              type="text"
              value={xdomeaFolderId}
              onChange={(e) => setXdomeaFolderId(e.target.value)}
            />
          </label>
          <label htmlFor="new-case-xdomea-process-definition">
            {t("cases.newCaseProcessDefinitionLabel")}
            <select
              id="new-case-xdomea-process-definition"
              value={xdomeaDefinitionId}
              onChange={(e) => setXdomeaDefinitionId(e.target.value)}
            >
              <option value="">{t("cases.newCaseProcessDefinitionPlaceholder")}</option>
              {definitions.map((def) => (
                <option key={def.id} value={def.id}>
                  {def.name}
                </option>
              ))}
            </select>
          </label>
          <span className="actions">
            <button
              type="button"
              disabled={
                xdomeaImporting || !xdomeaFile || !xdomeaFolderId.trim() || !xdomeaDefinitionId
              }
              onClick={handleXdomeaImport}
            >
              {xdomeaImporting ? t("cases.importing") : t("cases.importSubmit")}
            </button>
            <button type="button" onClick={() => setXdomeaOpen(false)}>
              {t("common.cancel")}
            </button>
          </span>
        </div>
      )}
      {xdomeaError && (
        <p className="error-text" role="alert">
          {xdomeaError}
        </p>
      )}

      <button type="button" onClick={() => setXjustizOpen((prev) => !prev)}>
        {t("cases.xjustizImport")}
      </button>
      {xjustizOpen && (
        <div className="inline-form">
          <label>
            {t("cases.importFileLabel")}
            <input type="file" onChange={(e) => setXjustizFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            {t("cases.importFolderLabel")}
            <input
              type="text"
              value={xjustizFolderId}
              onChange={(e) => setXjustizFolderId(e.target.value)}
            />
          </label>
          <label htmlFor="new-case-xjustiz-process-definition">
            {t("cases.newCaseProcessDefinitionLabel")}
            <select
              id="new-case-xjustiz-process-definition"
              value={xjustizDefinitionId}
              onChange={(e) => setXjustizDefinitionId(e.target.value)}
            >
              <option value="">{t("cases.newCaseProcessDefinitionPlaceholder")}</option>
              {definitions.map((def) => (
                <option key={def.id} value={def.id}>
                  {def.name}
                </option>
              ))}
            </select>
          </label>
          <span className="actions">
            <button
              type="button"
              disabled={
                xjustizImporting || !xjustizFile || !xjustizFolderId.trim() || !xjustizDefinitionId
              }
              onClick={handleXjustizImport}
            >
              {xjustizImporting ? t("cases.importing") : t("cases.importSubmit")}
            </button>
            <button type="button" onClick={() => setXjustizOpen(false)}>
              {t("common.cancel")}
            </button>
          </span>
        </div>
      )}
      {xjustizError && (
        <p className="error-text" role="alert">
          {xjustizError}
        </p>
      )}
    </>
  );
}

function CaseDetail({
  token,
  caseId,
  canArchive,
  onBack,
  onOpenDocument,
  isFavorite,
  onToggleFavorite,
  favoriteError,
}: {
  token: string;
  caseId: string;
  canArchive: boolean;
  onBack: () => void;
  onOpenDocument: (doc: DocumentSummary) => void;
  isFavorite: boolean;
  onToggleFavorite: () => void;
  favoriteError: string | null;
}) {
  const { t, locale } = useI18n();

  const [activeCase, setActiveCase] = useState<Case | null>(null);
  const [documents, setDocuments] = useState<CaseDocumentReference[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [xdomeaExportOpen, setXdomeaExportOpen] = useState(false);
  const [xdomeaLeserName, setXdomeaLeserName] = useState("");
  const [xdomeaExporting, setXdomeaExporting] = useState(false);
  const [xdomeaExportError, setXdomeaExportError] = useState<string | null>(null);

  const [xjustizExportOpen, setXjustizExportOpen] = useState(false);
  const [xjustizEmpfaengerName, setXjustizEmpfaengerName] = useState("");
  const [xjustizExporting, setXjustizExporting] = useState(false);
  const [xjustizExportError, setXjustizExportError] = useState<string | null>(null);

  const [xdomeaImportOpen, setXdomeaImportOpen] = useState(false);
  const [xdomeaImportFile, setXdomeaImportFile] = useState<File | null>(null);
  const [xdomeaImportFolderId, setXdomeaImportFolderId] = useState("root");
  const [xdomeaImporting, setXdomeaImporting] = useState(false);
  const [xdomeaImportError, setXdomeaImportError] = useState<string | null>(null);
  const [xdomeaImportSuccess, setXdomeaImportSuccess] = useState<string | null>(null);

  const [xjustizImportOpen, setXjustizImportOpen] = useState(false);
  const [xjustizImportFile, setXjustizImportFile] = useState<File | null>(null);
  const [xjustizImportFolderId, setXjustizImportFolderId] = useState("root");
  const [xjustizImporting, setXjustizImporting] = useState(false);
  const [xjustizImportError, setXjustizImportError] = useState<string | null>(null);
  const [xjustizImportSuccess, setXjustizImportSuccess] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [loadedCase, loadedDocuments] = await Promise.all([
        getCase(token, caseId),
        listCaseDocuments(token, caseId),
      ]);
      setActiveCase(loadedCase);
      setDocuments(loadedDocuments);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("cases.loadError"));
    }
  }, [token, caseId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleOpenCaseDocument(ref: CaseDocumentReference) {
    if (!token) return;
    try {
      onOpenDocument(await getDocument(token, ref.document_id));
    } catch {
      // 404 (deleted since the reference was added) - `document_deleted_at`
      // already reflects this in the list, nothing to open.
    }
  }

  async function handleXdomeaExport() {
    if (!token || !xdomeaLeserName.trim()) return;
    setXdomeaExportError(null);
    setXdomeaExporting(true);
    try {
      const blob = await exportCaseXdomea(token, caseId, xdomeaLeserName.trim());
      triggerBrowserDownload(blob, `${activeCase?.name ?? caseId}-abgabe.zip`);
      setXdomeaExportOpen(false);
      setXdomeaLeserName("");
    } catch {
      setXdomeaExportError(t("cases.xdomeaExportErrorGeneric"));
    } finally {
      setXdomeaExporting(false);
    }
  }

  async function handleXjustizExport() {
    if (!token || !xjustizEmpfaengerName.trim()) return;
    setXjustizExportError(null);
    setXjustizExporting(true);
    try {
      const blob = await exportCaseXjustiz(token, caseId, xjustizEmpfaengerName.trim());
      triggerBrowserDownload(blob, `${activeCase?.name ?? caseId}-xjustiz.zip`);
      setXjustizExportOpen(false);
      setXjustizEmpfaengerName("");
    } catch {
      setXjustizExportError(t("cases.xjustizExportErrorGeneric"));
    } finally {
      setXjustizExporting(false);
    }
  }

  async function handleXdomeaImport() {
    if (!token || !xdomeaImportFile || !xdomeaImportFolderId.trim()) return;
    setXdomeaImportError(null);
    setXdomeaImportSuccess(null);
    setXdomeaImporting(true);
    try {
      const result = await importXdomeaIntoCase(token, {
        file: xdomeaImportFile,
        folderId: xdomeaImportFolderId.trim(),
        caseId,
      });
      setXdomeaImportSuccess(t("cases.importSuccess", { count: result.document_ids.length }));
      setXdomeaImportOpen(false);
      setXdomeaImportFile(null);
      await reload();
    } catch (err) {
      setXdomeaImportError(
        err instanceof ApiError ? err.message : t("cases.xdomeaImportErrorGeneric")
      );
    } finally {
      setXdomeaImporting(false);
    }
  }

  async function handleXjustizImport() {
    if (!token || !xjustizImportFile || !xjustizImportFolderId.trim()) return;
    setXjustizImportError(null);
    setXjustizImportSuccess(null);
    setXjustizImporting(true);
    try {
      const result = await importXjustizIntoCase(token, {
        file: xjustizImportFile,
        folderId: xjustizImportFolderId.trim(),
        caseId,
      });
      setXjustizImportSuccess(t("cases.importSuccess", { count: result.document_ids.length }));
      setXjustizImportOpen(false);
      setXjustizImportFile(null);
      await reload();
    } catch (err) {
      setXjustizImportError(
        err instanceof ApiError ? err.message : t("cases.xjustizImportErrorGeneric")
      );
    } finally {
      setXjustizImporting(false);
    }
  }

  if (!activeCase) {
    return (
      <section className="cases-pane" aria-label={t("cases.paneLabel")}>
        <button type="button" onClick={onBack}>
          {t("cases.backToList")}
        </button>
        {error ? (
          <p className="error-text" role="alert">
            {error}
          </p>
        ) : (
          <p>{t("common.loading")}</p>
        )}
      </section>
    );
  }

  return (
    <section className="cases-pane" aria-label={t("cases.paneLabel")}>
      <button type="button" onClick={onBack}>
        {t("cases.backToList")}
      </button>
      <span className="heading-with-favorite">
        <h2 className="pane-heading">{activeCase.name}</h2>
        <button
          type="button"
          className="favorite-toggle"
          aria-label={
            isFavorite
              ? t("cases.removeFavorite", { name: activeCase.name })
              : t("cases.addFavorite", { name: activeCase.name })
          }
          onClick={onToggleFavorite}
        >
          {isFavorite ? "★" : "☆"}
        </button>
      </span>
      <p className="hint">
        {activeCase.vorgangsnummer ? `${activeCase.vorgangsnummer} · ` : ""}
        {activeCase.status === "open" ? t("cases.statusOpen") : t("cases.statusClosed")}
        {" · "}
        {t("cases.createdAt", { date: formatDate(activeCase.created_at, locale) })}
        {activeCase.closed_at
          ? ` · ${t("cases.closedAt", { date: formatDate(activeCase.closed_at, locale) })}`
          : ""}
      </p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {favoriteError && (
        <p className="error-text" role="alert">
          {favoriteError}
        </p>
      )}

      <h3>{t("cases.documentsHeading")}</h3>
      {documents.length === 0 ? (
        <p className="empty-state">{t("cases.documentsEmpty")}</p>
      ) : (
        <ul className="entry-list">
          {documents.map((ref) => (
            <li className="entry-row" key={ref.document_id}>
              {ref.document_deleted_at ? (
                <span className="entry-name">{t("cases.documentDeleted")}</span>
              ) : (
                <button
                  type="button"
                  className="entry-name"
                  onClick={() => handleOpenCaseDocument(ref)}
                >
                  {ref.document_id}
                </button>
              )}
              <span className="entry-meta">
                {t("cases.documentAddedAt", { date: formatDate(ref.added_at, locale) })}
                {ref.has_active_quarantine ? ` — ${t("cases.documentQuarantined")}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}

      {canArchive && (
        <>
          <h3>{t("cases.exportHeading")}</h3>
          {/* Case-level XDOMEA export (ADR 0126) - same inline-form shape
              as PreviewPane.tsx's document-level export button. */}
          <button type="button" onClick={() => setXdomeaExportOpen((prev) => !prev)}>
            {t("cases.xdomeaExport")}
          </button>
          {xdomeaExportOpen && (
            <div className="inline-form">
              <label>
                {t("cases.xdomeaExportLeserLabel")}
                <input
                  type="text"
                  value={xdomeaLeserName}
                  onChange={(e) => setXdomeaLeserName(e.target.value)}
                  placeholder={t("cases.xdomeaExportLeserPlaceholder")}
                />
              </label>
              <span className="actions">
                <button
                  type="button"
                  disabled={xdomeaExporting || !xdomeaLeserName.trim()}
                  onClick={handleXdomeaExport}
                >
                  {xdomeaExporting ? t("cases.exporting") : t("cases.exportSubmit")}
                </button>
                <button type="button" onClick={() => setXdomeaExportOpen(false)}>
                  {t("common.cancel")}
                </button>
              </span>
            </div>
          )}
          {xdomeaExportError && (
            <p className="error-text" role="alert">
              {xdomeaExportError}
            </p>
          )}

          {/* Case-level XJustiz export (ADR 0129) */}
          <button type="button" onClick={() => setXjustizExportOpen((prev) => !prev)}>
            {t("cases.xjustizExport")}
          </button>
          {xjustizExportOpen && (
            <div className="inline-form">
              <label>
                {t("cases.xjustizExportEmpfaengerLabel")}
                <input
                  type="text"
                  value={xjustizEmpfaengerName}
                  onChange={(e) => setXjustizEmpfaengerName(e.target.value)}
                  placeholder={t("cases.xjustizExportEmpfaengerPlaceholder")}
                />
              </label>
              <span className="actions">
                <button
                  type="button"
                  disabled={xjustizExporting || !xjustizEmpfaengerName.trim()}
                  onClick={handleXjustizExport}
                >
                  {xjustizExporting ? t("cases.exporting") : t("cases.xjustizExportSubmit")}
                </button>
                <button type="button" onClick={() => setXjustizExportOpen(false)}>
                  {t("common.cancel")}
                </button>
              </span>
            </div>
          )}
          {xjustizExportError && (
            <p className="error-text" role="alert">
              {xjustizExportError}
            </p>
          )}

          <h3>{t("cases.importHeading")}</h3>
          <p className="hint">{t("cases.importHint")}</p>
          {/* Case-level XDOMEA import (ADR 0128/0139) - deliberately always
              attaches to THIS case (`case_id`), never creates a new one -
              the "create a new case from an import" path lives instead on
              the case LIST view (`NewCaseImportSection`, Post-Roadmap Phase
              42 Session 1), which has no existing case to attach to. */}
          <button type="button" onClick={() => setXdomeaImportOpen((prev) => !prev)}>
            {t("cases.xdomeaImport")}
          </button>
          {xdomeaImportOpen && (
            <div className="inline-form">
              <label>
                {t("cases.importFileLabel")}
                <input
                  type="file"
                  onChange={(e) => setXdomeaImportFile(e.target.files?.[0] ?? null)}
                />
              </label>
              <label>
                {t("cases.importFolderLabel")}
                <input
                  type="text"
                  value={xdomeaImportFolderId}
                  onChange={(e) => setXdomeaImportFolderId(e.target.value)}
                />
              </label>
              <span className="actions">
                <button
                  type="button"
                  disabled={xdomeaImporting || !xdomeaImportFile || !xdomeaImportFolderId.trim()}
                  onClick={handleXdomeaImport}
                >
                  {xdomeaImporting ? t("cases.importing") : t("cases.importSubmit")}
                </button>
                <button type="button" onClick={() => setXdomeaImportOpen(false)}>
                  {t("common.cancel")}
                </button>
              </span>
            </div>
          )}
          {xdomeaImportError && (
            <p className="error-text" role="alert">
              {xdomeaImportError}
            </p>
          )}
          {xdomeaImportSuccess && (
            <p className="hint" role="status">
              {xdomeaImportSuccess}
            </p>
          )}

          {/* Case-level XJustiz import (ADR 0139) */}
          <button type="button" onClick={() => setXjustizImportOpen((prev) => !prev)}>
            {t("cases.xjustizImport")}
          </button>
          {xjustizImportOpen && (
            <div className="inline-form">
              <label>
                {t("cases.importFileLabel")}
                <input
                  type="file"
                  onChange={(e) => setXjustizImportFile(e.target.files?.[0] ?? null)}
                />
              </label>
              <label>
                {t("cases.importFolderLabel")}
                <input
                  type="text"
                  value={xjustizImportFolderId}
                  onChange={(e) => setXjustizImportFolderId(e.target.value)}
                />
              </label>
              <span className="actions">
                <button
                  type="button"
                  disabled={
                    xjustizImporting || !xjustizImportFile || !xjustizImportFolderId.trim()
                  }
                  onClick={handleXjustizImport}
                >
                  {xjustizImporting ? t("cases.importing") : t("cases.importSubmit")}
                </button>
                <button type="button" onClick={() => setXjustizImportOpen(false)}>
                  {t("common.cancel")}
                </button>
              </span>
            </div>
          )}
          {xjustizImportError && (
            <p className="error-text" role="alert">
              {xjustizImportError}
            </p>
          )}
          {xjustizImportSuccess && (
            <p className="hint" role="status">
              {xjustizImportSuccess}
            </p>
          )}
        </>
      )}
    </section>
  );
}
