"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  approveApprovalRequest,
  getSuperuserStatus,
  requestSuperuserActivation,
  type SuperuserStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Superuser Break-Glass (4.6, P6-S5): Aktivierung selbst läuft über den
// bereits bestehenden generischen Vier-Augen-Mechanismus des Permission
// Service (P6-S4, ADR 0022) - diese Seite ruft nur dessen Endpunkte auf und
// zeigt den Status (`GET /superuser/status`, Auth Service). Zwei
// *verschiedene* Personen, beide mit der Capability `breakglass.approve`
// (Rollenzuweisung über die bestehende Nutzer-/Rechteverwaltung-Seite),
// müssen anfordern und genehmigen - identisch mit jedem anderen gegateten
// Aktionstyp aus P6-S4, hier nur mit erzwungener Rollenbindung.
export function SuperuserBreakGlass() {
  const { user, accessToken } = useAuth();
  const { t } = useI18n();
  const [status, setStatus] = useState<SuperuserStatus | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [requestedId, setRequestedId] = useState<string | null>(null);
  const [approveRequestId, setApproveRequestId] = useState("");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setStatus(await getSuperuserStatus(accessToken));
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

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="error-text">{t("superuser.unreachable")}</p>;

  return (
    <div className="superuser-breakglass">
      <p className="hint">{t("superuser.hint")}</p>
      {error && <p className="error-text">{error}</p>}

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
        <button type="button" onClick={handleRequestActivation}>
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
          />
        </label>
        <button type="button" onClick={handleApprove} disabled={!approveRequestId}>
          {t("superuser.approveButton")}
        </button>
      </section>
    </div>
  );
}
