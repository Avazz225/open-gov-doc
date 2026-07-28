"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getGuardConfig,
  getGuardStatus,
  reidentifyTarget,
  updateGuardConfig,
  type GuardStatusEntry,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Kein Mehrfach-Ziele-Editor hier (Ziel-Set selbst ist Settings-JSON, kein
// Admin-UI-Formular, siehe ADR 0017) - nur der Wächter-Status (Geräte-ID je
// Ziel, offene Nachreplikation) und der Admin-Override, der einen
// degradierten Start beim nächsten Neustart erlaubt.
export function StorageGuard() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [statusEntries, setStatusEntries] = useState<GuardStatusEntry[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [allowDegradedStart, setAllowDegradedStart] = useState(false);
  const [reidentifyingTarget, setReidentifyingTarget] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [loadedConfig, loadedStatus] = await Promise.all([
        getGuardConfig(accessToken),
        getGuardStatus(accessToken),
      ]);
      setAllowDegradedStart(loadedConfig.allow_degraded_start);
      setStatusEntries(loadedStatus);
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

  async function handleReidentify(targetId: string) {
    if (!accessToken) return;
    if (!window.confirm(t("storageGuard.confirmReidentify", { targetId }))) return;
    setError(null);
    setReidentifyingTarget(targetId);
    try {
      await reidentifyTarget(accessToken, targetId);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setReidentifyingTarget(null);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    setSavedAt(null);
    setIsSaving(true);
    try {
      await updateGuardConfig(accessToken, allowDegradedStart);
      setSavedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("storageGuard.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="empty-state">{t("storageGuard.unreachable")}</p>
      ) : (
        <>
          <table className="data-table" style={{ marginBottom: "1rem" }}>
            <thead>
              <tr>
                <th>{t("storageGuard.targetId")}</th>
                <th>{t("storageGuard.deviceId")}</th>
                <th>{t("storageGuard.verifiedAt")}</th>
                <th>{t("storageGuard.pendingCopies")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {statusEntries.map((entry) => (
                <tr key={entry.target_id}>
                  <td>{entry.target_id}</td>
                  <td>{entry.device_id ?? t("storageGuard.deviceIdUnknown")}</td>
                  <td>{entry.verified_at ? new Date(entry.verified_at).toLocaleString() : "-"}</td>
                  <td>
                    <span className={`badge ${entry.pending_copies > 0 ? "down" : "ok"}`}>
                      {entry.pending_copies > 0
                        ? t("storageGuard.resyncing", { count: entry.pending_copies })
                        : t("storageGuard.inSync")}
                    </span>
                  </td>
                  <td>
                    <button
                      type="button"
                      onClick={() => handleReidentify(entry.target_id)}
                      disabled={reidentifyingTarget !== null}
                    >
                      {reidentifyingTarget === entry.target_id
                        ? t("common.loading")
                        : t("storageGuard.reidentify")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {statusEntries.length === 0 && <p className="empty-state">{t("storageGuard.empty")}</p>}

          <form onSubmit={handleSubmit}>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={allowDegradedStart}
                onChange={(event) => setAllowDegradedStart(event.target.checked)}
              />
              {t("storageGuard.allowDegradedStart")}
            </label>
            <div className="actions">
              <button type="submit" disabled={isSaving}>
                {t("common.save")}
              </button>
            </div>
          </form>
        </>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {savedAt !== null && !error && <p className="hint">{t("storageGuard.saved")}</p>}
    </div>
  );
}
