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

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const fieldLabel = "text-sm font-medium text-fg";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";

  return (
    <section>
      <div className="mb-6 flex items-center justify-between border-b border-border pb-3">
        <h1>{t("pairedInstallations.heading")}</h1>
        <button type="button" onClick={() => setShowForm((v) => !v)} className={secondaryBtn}>
          {t("pairedInstallations.newButton")}
        </button>
      </div>
      <p className="text-sm opacity-80">{t("pairedInstallations.hint")}</p>

      {createdApiKey && (
        <p className="text-success">
          {t("pairedInstallations.apiKeyNotice")} <code>{createdApiKey}</code>
        </p>
      )}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {showForm && (
        <form
          className="mt-2 flex max-w-[420px] flex-col gap-2 rounded-md border border-border p-3"
          onSubmit={handleCreate}
        >
          <label htmlFor="display-name" className={fieldLabel}>
            {t("pairedInstallations.displayNameLabel")}
          </label>
          <input
            id="display-name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            className={fieldInput}
          />
          <label htmlFor="base-url" className={fieldLabel}>
            {t("pairedInstallations.baseUrlLabel")}
          </label>
          <input
            id="base-url"
            type="url"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
            className={fieldInput}
          />
          <label htmlFor="api-key" className={fieldLabel}>
            {t("pairedInstallations.apiKeyLabel")}
          </label>
          <input
            id="api-key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className={fieldInput}
          />
          {formError && (
            <p className="text-danger" role="alert">
              {formError}
            </p>
          )}
          <div className="flex gap-2">
            <button type="submit" disabled={submitting} className={primaryBtn}>
              {submitting
                ? t("pairedInstallations.submitting")
                : t("pairedInstallations.submit")}
            </button>
            <button type="button" onClick={() => setShowForm(false)} className={secondaryBtn}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}

      {installations.length === 0 ? (
        <p className="italic opacity-70">{t("pairedInstallations.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("pairedInstallations.nameColumn")}</th>
              <th className={th}>{t("pairedInstallations.baseUrlColumn")}</th>
              <th className={th}>{t("pairedInstallations.createdColumn")}</th>
              <th className={th}>{t("pairedInstallations.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {installations.map((installation) => (
              <tr key={installation.id}>
                <td className={td}>{installation.display_name}</td>
                <td className={td}>{installation.base_url}</td>
                <td className={td}>{new Date(installation.created_at).toLocaleString("de-DE")}</td>
                <td className={td}>
                  <button
                    type="button"
                    onClick={() => handleDelete(installation.id)}
                    className={secondaryBtn}
                  >
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
