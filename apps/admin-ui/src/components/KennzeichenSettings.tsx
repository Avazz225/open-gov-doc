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

// Globaler Standard-Schalter für den Kennzeichengenerator (2.2, P5e-S3) -
// einzelnes Feld statt eines Katalogs mehrerer unabhängiger Anzeigepunkte
// (Tab-Titel, Listen-Präfix, ...), siehe PROGRESS.md "Kennzeichengenerator".
// Einzelne Dokumentenarten können diesen Standard über den Tri-State
// `kennzeichen_display_override` im Objekttyp-Editor überschreiben.
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

  return (
    <div className="card">
      <p className="hint">{t("kennzeichenSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("kennzeichenSettings.unreachable")}</p>
      ) : (
        <form onSubmit={handleSubmit}>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={showBeforeFilename}
              onChange={(event) => setShowBeforeFilename(event.target.checked)}
            />
            {t("kennzeichenSettings.showBeforeFilename")}
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
      {savedAt !== null && !error && <p className="hint">{t("kennzeichenSettings.saved")}</p>}
      {config && (
        <p className="hint">
          {t("kennzeichenSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
