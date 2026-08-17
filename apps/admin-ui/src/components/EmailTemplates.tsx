"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteEmailTemplate,
  listEmailTemplateUseCases,
  listEmailTemplates,
  putEmailTemplate,
  type EmailTemplate,
  type EmailTemplateUseCase,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Configurable email templates (post-roadmap phase 30, ADR 0111) - mirrors
// `ApprovalSettings`' load/toggle/create structure, but `use_case` is a
// fixed, closed catalog (`listEmailTemplateUseCases`) rather than free
// text, since `notification-service`'s `consumer.py` handlers are a fixed
// set of branches. A `use_case` can have more than one configured row: at
// most one catch-all (`recipient_domain_pattern = null`) plus any number of
// domain-specific overrides - the form below edits exactly one row at a
// time, identified by `(use_case, domain)`.
export function EmailTemplates() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [useCases, setUseCases] = useState<EmailTemplateUseCase[]>([]);
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const [formUseCase, setFormUseCase] = useState("");
  const [formDomain, setFormDomain] = useState("");
  const [formSubject, setFormSubject] = useState("");
  const [formBody, setFormBody] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [loadedUseCases, loadedTemplates] = await Promise.all([
        listEmailTemplateUseCases(accessToken),
        listEmailTemplates(accessToken),
      ]);
      setUseCases(loadedUseCases);
      setTemplates(
        [...loadedTemplates].sort(
          (a, b) =>
            a.use_case.localeCompare(b.use_case) ||
            (a.recipient_domain_pattern ?? "").localeCompare(b.recipient_domain_pattern ?? "")
        )
      );
      setFormUseCase((current) => current || loadedUseCases[0]?.use_case || "");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  function resetForm() {
    setEditingId(null);
    setFormDomain("");
    setFormSubject("");
    setFormBody("");
  }

  function handleEdit(template: EmailTemplate) {
    setEditingId(template.id);
    setFormUseCase(template.use_case);
    setFormDomain(template.recipient_domain_pattern ?? "");
    setFormSubject(template.subject_template);
    setFormBody(template.body_template);
  }

  async function handleDelete(template: EmailTemplate) {
    if (!accessToken) return;
    setError(null);
    setDeletingId(template.id);
    try {
      await deleteEmailTemplate(accessToken, template.id);
      if (editingId === template.id) resetForm();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("emailTemplates.deleteError"));
    } finally {
      setDeletingId(null);
    }
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !formUseCase || !formSubject.trim() || !formBody.trim()) return;
    setError(null);
    setIsSaving(true);
    try {
      await putEmailTemplate(accessToken, formUseCase, {
        recipientDomain: formDomain.trim() || null,
        subjectTemplate: formSubject,
        bodyTemplate: formBody,
      });
      resetForm();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("emailTemplates.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  const selectedUseCase = useCases.find((entry) => entry.use_case === formUseCase);

  return (
    <div className="card">
      <p className="hint">{t("emailTemplates.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("emailTemplates.unreachable")}</p>
      ) : (
        <>
          {templates.length === 0 ? (
            <p className="empty-state">{t("emailTemplates.empty")}</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>{t("emailTemplates.useCase")}</th>
                  <th>{t("emailTemplates.domain")}</th>
                  <th>{t("emailTemplates.subject")}</th>
                  <th>{t("emailTemplates.updatedAt")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {templates.map((template) => (
                  <tr key={template.id}>
                    <td>{template.use_case}</td>
                    <td>{template.recipient_domain_pattern ?? t("emailTemplates.domainCatchall")}</td>
                    <td>{template.subject_template}</td>
                    <td>{new Date(template.updated_at).toLocaleString()}</td>
                    <td>
                      <button type="button" onClick={() => handleEdit(template)}>
                        {t("emailTemplates.edit")}
                      </button>
                      <button
                        type="button"
                        disabled={deletingId !== null}
                        onClick={() => handleDelete(template)}
                      >
                        {t("common.delete")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <h2>{t("emailTemplates.formHeading")}</h2>
      <form
        aria-label={t("emailTemplates.formLabel")}
        className="form-grid"
        onSubmit={handleSave}
      >
        <label>
          {t("emailTemplates.useCase")}
          <select
            value={formUseCase}
            onChange={(e) => setFormUseCase(e.target.value)}
            disabled={editingId !== null}
            required
          >
            {useCases.map((entry) => (
              <option key={entry.use_case} value={entry.use_case}>
                {entry.use_case} - {entry.description}
              </option>
            ))}
          </select>
        </label>
        {selectedUseCase && (
          <p className="hint">
            {t("emailTemplates.placeholdersLabel")}:{" "}
            {selectedUseCase.placeholders.map((placeholder) => `{${placeholder}}`).join(", ")}
          </p>
        )}
        <label>
          {t("emailTemplates.domain")}
          <input
            value={formDomain}
            onChange={(e) => setFormDomain(e.target.value)}
            placeholder={t("emailTemplates.domainPlaceholder")}
            disabled={editingId !== null}
          />
        </label>
        <label>
          {t("emailTemplates.subject")}
          <input
            value={formSubject}
            onChange={(e) => setFormSubject(e.target.value)}
            placeholder={t("emailTemplates.subjectPlaceholder")}
            required
          />
        </label>
        <label>
          {t("emailTemplates.body")}
          <textarea
            value={formBody}
            onChange={(e) => setFormBody(e.target.value)}
            placeholder={t("emailTemplates.bodyPlaceholder")}
            rows={5}
            required
          />
        </label>
        <div>
          <button type="submit" disabled={isSaving}>
            {t("common.save")}
          </button>
          {editingId !== null && (
            <button type="button" onClick={resetForm}>
              {t("emailTemplates.cancelEdit")}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
