"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getShareLinkConfig,
  updateShareLinkConfig,
  type ShareLinkConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Public share link (4.2a, P14-S10) - per the concept wording, `enabled=false`
// means "no API route reachable" (implemented there as `404` instead of
// `403` on the affected document-service endpoints, see ADR 0047) - this
// also disables already-issued links, not just new ones.
export function ShareLinkSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<ShareLinkConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [enabled, setEnabled] = useState(true);
  const [maxValidityDaysInput, setMaxValidityDaysInput] = useState("30");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await getShareLinkConfig(accessToken);
      setConfig(loaded);
      setEnabled(loaded.enabled);
      setMaxValidityDaysInput(String(loaded.max_validity_days));
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

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    setSavedAt(null);
    setIsSaving(true);
    try {
      const updated = await updateShareLinkConfig(accessToken, {
        enabled,
        maxValidityDays: Number(maxValidityDaysInput),
      });
      setConfig(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("shareLinkSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("shareLinkSettings.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => setEnabled(event.target.checked)}
            />
            {t("shareLinkSettings.enabled")}
          </label>
          <label>
            {t("shareLinkSettings.maxValidityDays")}
            <input
              type="number"
              min={1}
              required
              value={maxValidityDaysInput}
              onChange={(event) => setMaxValidityDaysInput(event.target.value)}
            />
          </label>
          <div className="actions">
            <button type="submit" disabled={isSaving}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {savedAt !== null && !error && <p className="hint">{t("shareLinkSettings.saved")}</p>}
      {config && (
        <p className="hint">
          {t("shareLinkSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
