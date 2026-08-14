"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createPairedInstallation,
  deletePairedInstallation,
  listPairedInstallations,
  type PairedInstallation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Direct installation pairing instead of hub mediation (7.2, ADR 0034) - each
// row here is a possible target installation for `TransferConsole`.
export function PairedInstallationList() {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  const [installations, setInstallations] = useState<PairedInstallation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [createdApiKey, setCreatedApiKey] = useState<string | null>(null);

  const reload = () => {
    if (!accessToken) return;
    listPairedInstallations(accessToken)
      .then(setInstallations)
      .catch(() => setError(t("common.loadError")));
  };

  useEffect(reload, [accessToken, t]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setFormError(null);
    setSubmitting(true);
    try {
      const created = await createPairedInstallation(accessToken, {
        displayName,
        baseUrl,
        apiKey: apiKey || undefined,
      });
      setCreatedApiKey(created.api_key);
      setDisplayName("");
      setBaseUrl("");
      setApiKey("");
      setShowForm(false);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleDelete(id: string) {
    if (!accessToken) return;
    if (!window.confirm(t("pairedInstallations.deleteConfirm"))) return;
    try {
      await deletePairedInstallation(accessToken, id);
      reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  return (
    <section>
      <div className="top-bar">
        <h1>{t("pairedInstallations.heading")}</h1>
        <button type="button" onClick={() => setShowForm((v) => !v)}>
          {t("pairedInstallations.newButton")}
        </button>
      </div>
      <p className="hint">{t("pairedInstallations.hint")}</p>

      {createdApiKey && (
        <p className="success-text">
          {t("pairedInstallations.apiKeyNotice")} <code>{createdApiKey}</code>
        </p>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {showForm && (
        <form className="inline-form" onSubmit={handleCreate}>
          <label htmlFor="display-name">{t("pairedInstallations.displayNameLabel")}</label>
          <input
            id="display-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
          />
          <label htmlFor="base-url">{t("pairedInstallations.baseUrlLabel")}</label>
          <input
            id="base-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
          />
          <label htmlFor="api-key">{t("pairedInstallations.apiKeyLabel")}</label>
          <input id="api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
          {formError && (
            <p className="error-text" role="alert">
              {formError}
            </p>
          )}
          <div className="actions">
            <button type="submit" disabled={submitting}>
              {submitting
                ? t("pairedInstallations.submitting")
                : t("pairedInstallations.submit")}
            </button>
            <button type="button" onClick={() => setShowForm(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}

      {installations.length === 0 ? (
        <p className="empty-state">{t("pairedInstallations.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("pairedInstallations.nameColumn")}</th>
              <th>{t("pairedInstallations.baseUrlColumn")}</th>
              <th>{t("pairedInstallations.createdColumn")}</th>
              <th>{t("pairedInstallations.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {installations.map((installation) => (
              <tr key={installation.id}>
                <td>{installation.display_name}</td>
                <td>{installation.base_url}</td>
                <td>{new Date(installation.created_at).toLocaleString("de-DE")}</td>
                <td>
                  <button type="button" onClick={() => handleDelete(installation.id)}>
                    {t("common.delete")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
