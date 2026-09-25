"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getKennzeichenConfig,
  updateKennzeichenConfig,
  type KennzeichenConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Global default switch for the reference number generator (2.2, P5e-S3) -
// a single field instead of a catalog of several independent display points
// (tab title, list prefix, ...), see PROGRESS.md "Kennzeichengenerator".
// Individual document types can override this default via the tri-state
// `kennzeichen_display_override` in the object type editor.
export function KennzeichenSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<KennzeichenConfig | null>(null);
  const [showBeforeFilename, setShowBeforeFilename] = useState(true);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await getKennzeichenConfig(accessToken);
      setConfig(loaded);
      setShowBeforeFilename(loaded.show_before_filename);
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
      const updated = await updateKennzeichenConfig(accessToken, { showBeforeFilename });
      setConfig(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("kennzeichenSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="italic opacity-70">{t("kennzeichenSettings.unreachable")}</p>
      ) : (
        <form onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              className={checkbox}
              checked={showBeforeFilename}
              onChange={(event) => setShowBeforeFilename(event.target.checked)}
            />
            {t("kennzeichenSettings.showBeforeFilename")}
          </label>
          <div className="flex gap-2">
            <button type="submit" className={primaryBtn} disabled={isSaving}>
              {t("common.save")}
            </button>
          </div>
        </form>
      )}

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {savedAt !== null && !error && <p className="text-sm opacity-80">{t("kennzeichenSettings.saved")}</p>}
      {config && (
        <p className="text-sm opacity-80">
          {t("kennzeichenSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
