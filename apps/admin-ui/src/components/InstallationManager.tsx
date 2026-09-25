"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { useInstallation } from "@/lib/installation-context";

// Management of the installation list (P4-S5, concept 3a/8) - purely
// client-side in `localStorage` (see `lib/installations.ts`), no backend
// endpoint needed for this. Each installation needs its own login, but
// exactly once - after that, the `InstallationSwitcher` allows lossless
// switching.
export function InstallationManager() {
  const { t } = useI18n();
  const { installations, activeInstallation, addInstallation, removeInstallation, switchInstallation } =
    useInstallation();
  const [name, setName] = useState("");
  const [gatewayBaseUrl, setGatewayBaseUrlInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    const trimmedUrl = gatewayBaseUrl.trim();
    if (!trimmedName || !trimmedUrl) return;

    try {
      new URL(trimmedUrl);
    } catch {
      setError(t("installations.invalidUrl"));
      return;
    }

    setError(null);
    addInstallation({ name: trimmedName, gatewayBaseUrl: trimmedUrl });
    setName("");
    setGatewayBaseUrlInput("");
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <>
      <p className="text-sm opacity-80">{t("installations.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <section className="rounded-lg border border-border p-4 mb-6">
        <h2>{t("installations.newHeading")}</h2>
        <form aria-label={t("installations.formLabel")} onSubmit={handleSubmit}>
          <div className="form-grid">
            <label>
              {t("installations.name")}
              <input className={fieldInput} value={name} onChange={(e) => setName(e.target.value)} required />
            </label>
            <label>
              {t("installations.gatewayUrl")}
              <input
                type="url"
                className={fieldInput}
                value={gatewayBaseUrl}
                onChange={(e) => setGatewayBaseUrlInput(e.target.value)}
                placeholder="https://dms.beispiel.org:8009"
                required
              />
            </label>
          </div>
          <button type="submit" className={primaryBtn}>
            {t("common.create")}
          </button>
        </form>
      </section>

      <table className="data-table">
        <thead>
          <tr>
            <th>{t("installations.nameColumn")}</th>
            <th>{t("installations.gatewayUrlColumn")}</th>
            <th>{t("installations.statusColumn")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {installations.map((installation) => (
            <tr key={installation.id}>
              <td>{installation.name}</td>
              <td>{installation.gatewayBaseUrl}</td>
              <td>
                {installation.id === activeInstallation.id && (
                  <span className="badge ok">{t("installations.active")}</span>
                )}
              </td>
              <td className="flex gap-2">
                {installation.id !== activeInstallation.id && (
                  <button type="button" className={secondaryBtn} onClick={() => switchInstallation(installation.id)}>
                    {t("installations.switchTo")}
                  </button>
                )}
                <button
                  type="button"
                  className={secondaryBtn}
                  onClick={() => removeInstallation(installation.id)}
                  disabled={installations.length <= 1}
                >
                  {t("common.delete")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
