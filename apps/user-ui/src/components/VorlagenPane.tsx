"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  applyFolderTemplate,
  createFolderTemplate,
  deleteFolderTemplate,
  listFolderTemplates,
  type FolderTemplate,
} from "@/lib/api";

// Structure templates (2.5/7.3, P15-S6) - a folder subtree as a named,
// reusable template (e.g. a file-plan skeleton). As with
// QuarantinePane/PoststellePane (P15-S2/S3), folder IDs are deliberately
// captured as raw text input rather than through a purpose-built tree
// picker - an established simplification accepted in this project, see
// ADR 0056.
export function VorlagenPane({ token, createdBy }: { token: string; createdBy: string }) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<FolderTemplate[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [sourceFolderId, setSourceFolderId] = useState("");
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  const [applyingId, setApplyingId] = useState<string | null>(null);
  const [targetParentId, setTargetParentId] = useState("");

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setTemplates(await listFolderTemplates(token));
    } catch {
      setError(t("vorlagen.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!sourceFolderId.trim() || !newName.trim()) return;
    setIsCreating(true);
    setError(null);
    try {
      await createFolderTemplate(token, {
        sourceFolderId: sourceFolderId.trim(),
        name: newName.trim(),
        description: newDescription.trim(),
        createdBy,
      });
      setSourceFolderId("");
      setNewName("");
      setNewDescription("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("vorlagen.createError"));
    } finally {
      setIsCreating(false);
    }
  }

  async function handleDelete(template: FolderTemplate) {
    if (!window.confirm(t("vorlagen.deleteConfirm", { name: template.name }))) return;
    setBusyId(template.id);
    setError(null);
    try {
      await deleteFolderTemplate(token, template.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("vorlagen.deleteError"));
    } finally {
      setBusyId(null);
    }
  }

  function startApply(template: FolderTemplate) {
    setApplyingId(template.id);
    setTargetParentId("");
    setError(null);
  }

  async function confirmApply(template: FolderTemplate) {
    if (!targetParentId.trim()) return;
    setBusyId(template.id);
    setError(null);
    try {
      await applyFolderTemplate(token, template.id, {
        targetParentId: targetParentId.trim(),
        createdBy,
      });
      setApplyingId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("vorlagen.applyError"));
    } finally {
      setBusyId(null);
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <section className="vorlagen-pane" aria-label={t("vorlagen.paneLabel")}>
      <h2 className="m-0 mb-3 text-base">{t("vorlagen.heading")}</h2>
      <p className="text-sm opacity-80">{t("vorlagen.hint")}</p>

      <form onSubmit={handleCreate}>
        <label>
          {t("vorlagen.sourceFolderLabel")}
          <input
            type="text"
            className={fieldInput}
            value={sourceFolderId}
            onChange={(e) => setSourceFolderId(e.target.value)}
            placeholder={t("vorlagen.sourceFolderPlaceholder")}
          />
        </label>
        <label>
          {t("vorlagen.nameLabel")}
          <input
            type="text"
            className={fieldInput}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
        </label>
        <label>
          {t("vorlagen.descriptionLabel")}
          <input
            type="text"
            className={fieldInput}
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
        </label>
        <button
          type="submit"
          className={primaryBtn}
          disabled={isCreating || !sourceFolderId.trim() || !newName.trim()}
        >
          {t("vorlagen.create")}
        </button>
      </form>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : templates.length === 0 ? (
        <p className="italic opacity-70">{t("vorlagen.empty")}</p>
      ) : (
        <ul className="entry-list">
          {templates.map((template) => (
            <li className="entry-row" key={template.id}>
              <span className="entry-name">
                {template.name}
                {template.description ? ` — ${template.description}` : ""}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  className={secondaryBtn}
                  onClick={() => startApply(template)}
                  disabled={busyId === template.id}
                >
                  {t("vorlagen.apply")}
                </button>
                <button
                  type="button"
                  className={secondaryBtn}
                  onClick={() => handleDelete(template)}
                  disabled={busyId === template.id}
                >
                  {t("vorlagen.delete")}
                </button>
              </span>
              {applyingId === template.id && (
                <div className="vorlagen-apply-form">
                  <label>
                    {t("vorlagen.targetFolderLabel")}
                    <input
                      type="text"
                      className={fieldInput}
                      value={targetParentId}
                      onChange={(e) => setTargetParentId(e.target.value)}
                      placeholder={t("vorlagen.targetFolderPlaceholder")}
                    />
                  </label>
                  <span className="flex gap-2">
                    <button
                      type="button"
                      className={primaryBtn}
                      onClick={() => confirmApply(template)}
                      disabled={busyId === template.id || !targetParentId.trim()}
                    >
                      {t("vorlagen.applyConfirm")}
                    </button>
                    <button type="button" className={secondaryBtn} onClick={() => setApplyingId(null)}>
                      {t("vorlagen.applyCancel")}
                    </button>
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
