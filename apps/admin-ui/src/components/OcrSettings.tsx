"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getOcrConfig, updateOcrConfig, type OcrConfig } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// ocrEnabled (3.9) ist bewusst NICHT hier editierbar - das ist ein Docker-
// Compose-Profil-Opt-out (ADR 0016): der Container wird gar nicht deployt,
// eine bereits laufende Instanz kann sich nicht selbst "undeployen". Ist der
// Service nicht erreichbar, zeigt diese Seite genau das an (unreachable
// statt eines Fehlers) - das ist zugleich der sichtbare Status von
// "ocrEnabled=false" in dieser UI, ohne einen eigenen Schalter dafür.
export function OcrSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<OcrConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [maxWordCountInput, setMaxWordCountInput] = useState("");
  const [batchSizeInput, setBatchSizeInput] = useState("4");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await getOcrConfig(accessToken);
      setConfig(loaded);
      setMaxWordCountInput(loaded.max_word_count === null ? "" : String(loaded.max_word_count));
      setBatchSizeInput(String(loaded.batch_size));
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
      const maxWordCount = maxWordCountInput.trim() === "" ? null : Number(maxWordCountInput);
      const updated = await updateOcrConfig(accessToken, {
        maxWordCount,
        batchSize: Number(batchSizeInput),
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
      <p className="hint">{t("ocrSettings.enabledHint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("ocrSettings.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            {t("ocrSettings.maxWordCount")}
            <input
              type="number"
              min={1}
              placeholder={t("ocrSettings.maxWordCountPlaceholder")}
              value={maxWordCountInput}
              onChange={(event) => setMaxWordCountInput(event.target.value)}
            />
          </label>
          <label>
            {t("ocrSettings.batchSize")}
            <input
              type="number"
              min={1}
              max={64}
              required
              value={batchSizeInput}
              onChange={(event) => setBatchSizeInput(event.target.value)}
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
      {savedAt !== null && !error && <p className="hint">{t("ocrSettings.saved")}</p>}
      {config && (
        <p className="hint">
          {t("ocrSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
