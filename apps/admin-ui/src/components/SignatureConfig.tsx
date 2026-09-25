"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getSignatureConfig,
  updateSignatureConfig,
  type SignatureProviderStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ALL_LEVELS = ["ses", "aes", "qes"] as const;

// Connector levels (3.10, Post-Roadmap Phase 22 Session 6, ADR 0091) - `id`/
// `type` are structurally fixed (env var), only `levels` per connector is
// admin-editable. Takes effect immediately on the next signing operation, no
// restart of the Signature Service required.
export function SignatureConfig() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [providers, setProviders] = useState<SignatureProviderStatus[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setProviders(await getSignatureConfig(accessToken));
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

  async function handleToggleLevel(
    provider: SignatureProviderStatus,
    level: (typeof ALL_LEVELS)[number]
  ) {
    if (!accessToken) return;
    const levels = provider.levels.includes(level)
      ? provider.levels.filter((l) => l !== level)
      : [...provider.levels, level];
    if (levels.length === 0) return;
    setError(null);
    setSavingId(provider.id);
    try {
      await updateSignatureConfig(accessToken, [{ id: provider.id, levels }]);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("signatureConfig.saveError"));
    } finally {
      setSavingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="italic opacity-70">{t("signatureConfig.unreachable")}</p>;

  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("signatureConfig.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {providers.length === 0 ? (
        <p className="italic opacity-70">{t("signatureConfig.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("signatureConfig.connectorId")}</th>
              <th>{t("signatureConfig.type")}</th>
              <th>{t("signatureConfig.levels")}</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((provider) => (
              <tr key={provider.id}>
                <td>{provider.id}</td>
                <td>{provider.type}</td>
                <td>
                  {ALL_LEVELS.map((level) => (
                    <label key={level} className="checkbox-label">
                      <input
                        type="checkbox"
                        className={checkbox}
                        checked={provider.levels.includes(level)}
                        disabled={savingId !== null}
                        onChange={() => handleToggleLevel(provider, level)}
                      />
                      {level.toUpperCase()}
                    </label>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
