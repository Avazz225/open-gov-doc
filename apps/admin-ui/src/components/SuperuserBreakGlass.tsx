"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  approveApprovalRequest,
  getMaintenanceStatus,
  getSuperuserStatus,
  liftMaintenanceMode,
  requestSuperuserActivation,
  triggerMaintenanceMode,
  type MaintenanceMode,
  type SuperuserStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Superuser break-glass (4.6, P6-S5): activation itself runs through the
// already-existing generic four-eyes principle mechanism of the Permission
// Service (P6-S4, ADR 0022) - this page only calls its endpoints and
// displays the status (`GET /superuser/status`, Auth Service). Two
// *different* people, both holding the `breakglass.approve` capability
// (role assignment via the existing user/permission management page), must
// request and approve - identical to any other gated action type from
// P6-S4, just with an enforced role binding here.
export function SuperuserBreakGlass() {
  const { user, permissions, accessToken } = useAuth();
  const { t } = useI18n();
  const [status, setStatus] = useState<SuperuserStatus | null>(null);
  const [maintenance, setMaintenance] = useState<MaintenanceMode | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [requestedId, setRequestedId] = useState<string | null>(null);
  const [approveRequestId, setApproveRequestId] = useState("");
  const [shutdownReason, setShutdownReason] = useState("");
  const [shutdownResult, setShutdownResult] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      const [superuserStatus, maintenanceStatus] = await Promise.all([
        getSuperuserStatus(accessToken),
        getMaintenanceStatus(accessToken),
      ]);
      setStatus(superuserStatus);
      setMaintenance(maintenanceStatus);
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

  async function handleRequestActivation() {
    if (!accessToken || !user) return;
    setError(null);
    try {
      const request = await requestSuperuserActivation(accessToken, user.sub);
      setRequestedId(request.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function handleApprove() {
    if (!accessToken || !user || !approveRequestId) return;
    setError(null);
    try {
      await approveApprovalRequest(accessToken, approveRequestId, user.sub);
      setApproveRequestId("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function handleTriggerShutdown() {
    if (!accessToken || !user) return;
    setError(null);
    try {
      const result = await triggerMaintenanceMode(accessToken, user.sub, shutdownReason);
      setShutdownResult(result.status);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function handleLiftShutdown() {
    if (!accessToken || !user) return;
    setError(null);
    try {
      await liftMaintenanceMode(accessToken, user.sub);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="text-danger">{t("superuser.unreachable")}</p>;

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <div className="superuser-breakglass">
      <p className="text-sm opacity-80">{t("superuser.hint")}</p>
      {error && <p className="text-danger">{error}</p>}

      <section>
        <h2>{t("superuser.statusTitle")}</h2>
        <p>
          <strong>
            {status?.active ? t("superuser.statusActive") : t("superuser.statusInactive")}
          </strong>
          {status?.active && status.expires_at && (
            <> — {t("superuser.expiresAt")}: {status.expires_at}</>
          )}
        </p>
      </section>

      <section>
        <h2>{t("superuser.requestTitle")}</h2>
        <button type="button" onClick={handleRequestActivation} className={primaryBtn}>
          {t("superuser.requestButton")}
        </button>
        {requestedId && <p>{t("superuser.requestSent", { id: requestedId })}</p>}
      </section>

      <section>
        <h2>{t("superuser.approveTitle")}</h2>
        <label>
          {t("superuser.requestIdLabel")}
          <input
            value={approveRequestId}
            onChange={(e) => setApproveRequestId(e.target.value)}
            className={fieldInput}
          />
        </label>
        <button
          type="button"
          onClick={handleApprove}
          disabled={!approveRequestId}
          className={primaryBtn}
        >
          {t("superuser.approveButton")}
        </button>
      </section>

      <section>
        <h2>{t("notShutdown.title")}</h2>
        <p>
          <strong>
            {maintenance?.active ? t("notShutdown.statusActive") : t("notShutdown.statusInactive")}
          </strong>
          {maintenance?.active && maintenance.triggered_by && (
            <> — {t("notShutdown.triggeredBy")}: {maintenance.triggered_by}</>
          )}
        </p>
        {permissions.includes("system.not_shutdown.trigger") && !maintenance?.active && (
          <div>
            <label>
              {t("notShutdown.reasonLabel")}
              <input
                value={shutdownReason}
                onChange={(e) => setShutdownReason(e.target.value)}
                className={fieldInput}
              />
            </label>
            <button type="button" onClick={handleTriggerShutdown} className={primaryBtn}>
              {t("notShutdown.triggerButton")}
            </button>
            {shutdownResult && <p>{t(`notShutdown.result.${shutdownResult}`)}</p>}
          </div>
        )}
        {maintenance?.active && status?.active && status.principal_id === user?.sub && (
          <button type="button" onClick={handleLiftShutdown} className={primaryBtn}>
            {t("notShutdown.liftButton")}
          </button>
        )}
      </section>
    </div>
  );
}
