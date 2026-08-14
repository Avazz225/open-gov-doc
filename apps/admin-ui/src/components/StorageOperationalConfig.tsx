"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getOperationalConfig, updateOperationalConfig } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Operational parameters of the target set (3.6, Post-Roadmap Phase 22
// Session 6, ADR 0091) - unlike the target set itself (credentials, remain
// env-var-only), these contain no secrets, so they're live-editable. A
// change takes effect immediately on the next upload/retry run, no restart
// of the Storage Service required.
export function StorageOperationalConfig() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const [writeStrategy, setWriteStrategy] = useState<"quorum" | "primary_async">("primary_async");
  const [quorumCount, setQuorumCount] = useState("1");
  const [maxReplicationAttempts, setMaxReplicationAttempts] = useState("5");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const config = await getOperationalConfig(accessToken);
      setWriteStrategy(config.write_strategy);
      setQuorumCount(String(config.quorum_count));
      setMaxReplicationAttempts(String(config.max_replication_attempts));
      setUpdatedAt(config.updated_at);
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
      const config = await updateOperationalConfig(accessToken, {
        writeStrategy,
        quorumCount: Number(quorumCount),
        maxReplicationAttempts: Number(maxReplicationAttempts),
      });
      setUpdatedAt(config.updated_at);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("storageOperationalConfig.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("storageOperationalConfig.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("storageOperationalConfig.unreachable")}</p>
      ) : (
        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            {t("storageOperationalConfig.writeStrategy")}
            <select
              value={writeStrategy}
              onChange={(event) => setWriteStrategy(event.target.value as "quorum" | "primary_async")}
            >
              <option value="primary_async">{t("storageOperationalConfig.strategyPrimaryAsync")}</option>
              <option value="quorum">{t("storageOperationalConfig.strategyQuorum")}</option>
            </select>
          </label>
          <label>
            {t("storageOperationalConfig.quorumCount")}
            <input
              type="number"
              min={1}
              required
              value={quorumCount}
              onChange={(event) => setQuorumCount(event.target.value)}
            />
          </label>
          <label>
            {t("storageOperationalConfig.maxReplicationAttempts")}
            <input
              type="number"
              min={1}
              required
              value={maxReplicationAttempts}
              onChange={(event) => setMaxReplicationAttempts(event.target.value)}
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
      {savedAt !== null && !error && <p className="hint">{t("storageOperationalConfig.saved")}</p>}
      {updatedAt && (
        <p className="hint">
          {t("storageOperationalConfig.updatedAt")}: {new Date(updatedAt).toLocaleString()}
        </p>
      )}
    </div>
  );
}
