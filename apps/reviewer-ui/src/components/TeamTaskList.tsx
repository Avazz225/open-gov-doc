"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listDirectReports,
  listReadyTasks,
  type ReadyTaskWithInstance,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Supervisor/team task oversight view (14.2, post-roadmap phase 31 session
// 11) - a SEPARATE, org-hierarchy-aware view alongside TaskList.tsx (which
// stays a flat, instance-agnostic list for the current user, ADR 0041/8).
// Purely read-only: shows every currently open task claimed by one of the
// logged-in person's direct reports (P31-S9's `SupervisorAssignment`, put to
// use for oversight the same way session 10 put it to use for access
// grants). Deliberately does NOT offer completion/claim/grant actions here -
// a manager viewing a report's work is not the same as acting on it; the
// existing "on behalf of" delegation (ADR 0048) or the org-hierarchy grant
// (session 10) already cover the "act for someone else" case, on
// TaskList.tsx, not duplicated here.
export function TeamTaskList({
  onOpenInstance,
}: {
  onOpenInstance: (instanceId: string) => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [directReportIds, setDirectReportIds] = useState<string[] | null>(null);
  const [tasks, setTasks] = useState<ReadyTaskWithInstance[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken || !user) return;
    listDirectReports(accessToken, user.sub)
      .then((assignments) => setDirectReportIds(assignments.map((a) => a.principal_id)))
      .catch(() => setError(t("common.loadError")));
  }, [accessToken, user, t]);

  useEffect(() => {
    if (!accessToken) return;
    listReadyTasks(accessToken)
      .then(setTasks)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [accessToken, t]);

  const teamTasks =
    directReportIds === null
      ? []
      : tasks.filter((task) => task.claimed_by !== null && directReportIds.includes(task.claimed_by));

  return (
    <section>
      <h1>{t("teamTaskList.heading")}</h1>
      <p className="hint">{t("teamTaskList.hint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {directReportIds !== null && directReportIds.length === 0 ? (
        <p className="empty-state">{t("teamTaskList.noDirectReports")}</p>
      ) : teamTasks.length === 0 ? (
        <p className="empty-state">{t("teamTaskList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("taskList.nameColumn")}</th>
              <th>{t("taskList.processColumn")}</th>
              <th>{t("taskList.businessKeyColumn")}</th>
              <th>{t("teamTaskList.claimedByColumn")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {teamTasks.map((task) => (
              <tr key={task.id}>
                <td>{task.name}</td>
                <td>#{task.process_definition_id}</td>
                <td>{task.business_key ?? "-"}</td>
                <td>{task.claimed_by}</td>
                <td>
                  <button type="button" onClick={() => onOpenInstance(task.instance_id)}>
                    {t("taskList.instanceLink")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
