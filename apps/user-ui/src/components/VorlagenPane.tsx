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

// Struktur-Vorlagen (2.5/7.3, P15-S6) - ein Ordner-Teilbaum als benannte,
// wiederverwendbare Vorlage (z. B. Aktenplan-Rohbau). Wie bei
// QuarantinePane/PoststellePane (P15-S2/S3) werden Ordner-IDs bewusst als
// rohe Texteingabe erfasst statt über einen eigens gebauten Baum-Picker -
// etablierte, in diesem Projekt akzeptierte Vereinfachung, siehe ADR 0056.
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

  return (
    <section className="vorlagen-pane" aria-label={t("vorlagen.paneLabel")}>
      <h2 className="pane-heading">{t("vorlagen.heading")}</h2>
      <p className="hint">{t("vorlagen.hint")}</p>

      <form onSubmit={handleCreate}>
        <label>
          {t("vorlagen.sourceFolderLabel")}
          <input
            type="text"
            value={sourceFolderId}
            onChange={(e) => setSourceFolderId(e.target.value)}
            placeholder={t("vorlagen.sourceFolderPlaceholder")}
          />
        </label>
        <label>
          {t("vorlagen.nameLabel")}
          <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)} />
        </label>
        <label>
          {t("vorlagen.descriptionLabel")}
          <input
            type="text"
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
        </label>
        <button
          type="submit"
          disabled={isCreating || !sourceFolderId.trim() || !newName.trim()}
        >
          {t("vorlagen.create")}
        </button>
      </form>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : templates.length === 0 ? (
        <p className="empty-state">{t("vorlagen.empty")}</p>
      ) : (
        <ul className="entry-list">
          {templates.map((template) => (
            <li className="entry-row" key={template.id}>
              <span className="entry-name">
                {template.name}
                {template.description ? ` — ${template.description}` : ""}
              </span>
              <span className="actions">
                <button
                  type="button"
                  onClick={() => startApply(template)}
                  disabled={busyId === template.id}
                >
                  {t("vorlagen.apply")}
                </button>
                <button
                  type="button"
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
                      value={targetParentId}
                      onChange={(e) => setTargetParentId(e.target.value)}
                      placeholder={t("vorlagen.targetFolderPlaceholder")}
                    />
                  </label>
                  <span className="actions">
                    <button
                      type="button"
                      onClick={() => confirmApply(template)}
                      disabled={busyId === template.id || !targetParentId.trim()}
                    >
                      {t("vorlagen.applyConfirm")}
                    </button>
                    <button type="button" onClick={() => setApplyingId(null)}>
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
