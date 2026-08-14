"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, listAllTeamspaces, type TeamspaceAdmin } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Teamspaces admin overview (Post-Roadmap Phase 22 Session 5) - a plain
// status table against the new `teamspace-service` endpoint
// (`GET /admin/teamspaces`, installation-wide instead of filtered by
// membership like `GET /teamspaces`). Creation/deletion/member management
// deliberately remains self-service (2.5) - no administrative actions here,
// visibility only.
export function TeamspacesAdmin() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [teamspaces, setTeamspaces] = useState<TeamspaceAdmin[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setTeamspaces(await listAllTeamspaces(accessToken));
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

  if (isLoading) return <p>{t("common.loading")}</p>;
  if (unreachable) return <p className="empty-state">{t("teamspacesAdmin.unreachable")}</p>;

  return (
    <div className="card">
      <p className="hint">{t("teamspacesAdmin.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {teamspaces.length === 0 ? (
        <p className="empty-state">{t("teamspacesAdmin.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("teamspacesAdmin.name")}</th>
              <th>{t("teamspacesAdmin.description")}</th>
              <th>{t("teamspacesAdmin.createdBy")}</th>
              <th>{t("teamspacesAdmin.memberCount")}</th>
              <th>{t("teamspacesAdmin.createdAt")}</th>
            </tr>
          </thead>
          <tbody>
            {teamspaces.map((teamspace) => (
              <tr key={teamspace.id}>
                <td>{teamspace.name}</td>
                <td>{teamspace.description || "—"}</td>
                <td>{teamspace.created_by}</td>
                <td>{teamspace.member_count}</td>
                <td>{new Date(teamspace.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
