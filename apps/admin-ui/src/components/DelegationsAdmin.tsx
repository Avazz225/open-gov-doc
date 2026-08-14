"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, listAllDelegations, revokeDelegationAsAdmin, type Delegation } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function isActive(delegation: Delegation): boolean {
  if (delegation.revoked_at !== null) return false;
  const now = Date.now();
  return (
    new Date(delegation.starts_at).getTime() <= now && now <= new Date(delegation.ends_at).getTime()
  );
}

function statusLabel(t: (path: string) => string, delegation: Delegation): string {
  if (delegation.revoked_at !== null) return t("delegationsAdmin.statusRevoked");
  if (isActive(delegation)) return t("delegationsAdmin.statusActive");
  if (new Date(delegation.ends_at).getTime() < Date.now())
    return t("delegationsAdmin.statusExpired");
  return t("delegationsAdmin.statusPending");
}

// Delegation during absence (4.4a, P14-S11) - a pure admin overview of ALL
// delegations recorded installation-wide (`GET /delegations` without a
// filter), with a revoke option for an authorized admin role
// (a pattern like `share_link_revoke_admin_role`, here
// `delegation_revoke_admin_role`, checked server-side in permission-service
// - this page itself gates nothing additionally, the same principle as
// ArchivalTransfersView).
export function DelegationsAdmin() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [delegations, setDelegations] = useState<Delegation[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [revokingId, setRevokingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setDelegations(await listAllDelegations(accessToken));
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

  async function handleRevoke(delegation: Delegation) {
    if (!accessToken) return;
    if (!window.confirm(t("delegationsAdmin.revokeConfirm"))) return;
    setRevokingId(delegation.id);
    setError(null);
    try {
      await revokeDelegationAsAdmin(accessToken, delegation.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("delegationsAdmin.revokeError"));
    } finally {
      setRevokingId(null);
    }
  }

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("delegationsAdmin.unreachable")}</p>;

  return (
    <div className="card">
      <p className="hint">{t("delegationsAdmin.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {delegations.length === 0 ? (
        <p className="empty-state">{t("delegationsAdmin.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("delegationsAdmin.delegator")}</th>
              <th>{t("delegationsAdmin.deputy")}</th>
              <th>{t("delegationsAdmin.startsAt")}</th>
              <th>{t("delegationsAdmin.endsAt")}</th>
              <th>{t("delegationsAdmin.status")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {delegations.map((delegation) => (
              <tr key={delegation.id}>
                <td>{delegation.delegator_principal_id}</td>
                <td>{delegation.deputy_principal_id}</td>
                <td>{new Date(delegation.starts_at).toLocaleString()}</td>
                <td>{new Date(delegation.ends_at).toLocaleString()}</td>
                <td>
                  <span
                    className={`badge ${isActive(delegation) ? "ok" : "down"}`}
                  >
                    {statusLabel(t, delegation)}
                  </span>
                </td>
                <td>
                  {isActive(delegation) && (
                    <button
                      type="button"
                      onClick={() => handleRevoke(delegation)}
                      disabled={revokingId !== null}
                    >
                      {revokingId === delegation.id
                        ? t("common.loading")
                        : t("delegationsAdmin.revoke")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
