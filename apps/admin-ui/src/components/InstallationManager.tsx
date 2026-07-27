"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { useInstallation } from "@/lib/installation-context";

// Verwaltung der Installationsliste (P4-S5, Konzept 3a/8) - rein clientseitig
// in `localStorage` (siehe `lib/installations.ts`), kein Backend-Endpunkt
// dafür nötig. Jede Installation braucht eine eigene Anmeldung, aber genau
// einmal - danach erlaubt der `InstallationSwitcher` verlustfreies Wechseln.
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

  return (
    <>
      <p className="hint">{t("installations.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <section className="card">
        <h2>{t("installations.newHeading")}</h2>
        <form aria-label={t("installations.formLabel")} onSubmit={handleSubmit}>
          <div className="form-grid">
            <label>
              {t("installations.name")}
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </label>
            <label>
              {t("installations.gatewayUrl")}
              <input
                type="url"
                value={gatewayBaseUrl}
                onChange={(e) => setGatewayBaseUrlInput(e.target.value)}
                placeholder="https://dms.beispiel.org:8009"
                required
              />
            </label>
          </div>
          <button type="submit">{t("common.create")}</button>
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
              <td className="actions">
                {installation.id !== activeInstallation.id && (
                  <button type="button" onClick={() => switchInstallation(installation.id)}>
                    {t("installations.switchTo")}
                  </button>
                )}
                <button
                  type="button"
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
