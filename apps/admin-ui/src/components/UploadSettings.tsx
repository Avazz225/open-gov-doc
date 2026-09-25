"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getUploadConfig, updateUploadConfig, type UploadConfig } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export function UploadSettings() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<UploadConfig | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const [allowedContentTypesInput, setAllowedContentTypesInput] = useState("");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const loaded = await getUploadConfig(accessToken);
      setConfig(loaded);
      setAllowedContentTypesInput(loaded.allowed_content_types.join(", "));
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
      const allowedContentTypes = allowedContentTypesInput
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value.length > 0);
      const updated = await updateUploadConfig(accessToken, { allowedContentTypes });
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
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <div className="rounded-lg border border-border p-4 mb-6">
      <p className="text-sm opacity-80">{t("uploadSettings.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="italic opacity-70">{t("uploadSettings.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            {t("uploadSettings.allowedContentTypes")}
            <input
              type="text"
              className={fieldInput}
              placeholder="application/pdf, text/plain, application/json"
              value={allowedContentTypesInput}
              onChange={(event) => setAllowedContentTypesInput(event.target.value)}
            />
          </label>
          <p className="col-span-full text-sm opacity-80">{t("uploadSettings.allowedContentTypesHint")}</p>
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
      {savedAt !== null && !error && <p className="text-sm opacity-80">{t("uploadSettings.saved")}</p>}
      {config && (
        <p className="text-sm opacity-80">
          {t("uploadSettings.updatedAt")}: {new Date(config.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  );
}
