"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  claimTask,
  listDirectReports,
  listReadyTasks,
  reassignTask,
  type ReadyTaskWithInstance,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Supervisor/team task oversight view (14.2, post-roadmap phase 31 session
// 11) - a SEPARATE, org-hierarchy-aware view alongside TaskList.tsx (which
// stays a flat, instance-agnostic list for the current user, ADR 0041/8).
// Shows every currently open task claimed by one of the logged-in person's
// direct reports (P31-S9's `SupervisorAssignment`, put to use for oversight
// the same way session 10 put it to use for access grants), PLUS - since
// Post-Roadmap Phase 35 Session 3 (ADR 0145) - every UNCLAIMED task whose
// process instance was started by a direct report (the only attribution
// signal an unclaimed task has, `GET /tasks` already returns it, just
// unused by this view until now, ADR 0122's own documented scope limit).
// Originally documented as "purely read-only... a manager viewing a
// report's work is not the same as acting on it" (ADR 0122) - ADR 0145
// deliberately narrows that stance: a supervisor may now assign an
// unclaimed team task or reassign a claimed one, but still NOT complete a
// task here - that remains TaskList.tsx's/the existing "on behalf of"
// delegation's territory.
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
  const [actionError, setActionError] = useState<string | null>(null);
  const [openFormTaskId, setOpenFormTaskId] = useState<string | null>(null);
  const [principalIdInput, setPrincipalIdInput] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reloadTasks = useCallback(() => {
    if (!accessToken) return;
    listReadyTasks(accessToken)
      .then(setTasks)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [accessToken, t]);

  useEffect(() => {
    if (!accessToken || !user) return;
    listDirectReports(accessToken, user.sub)
      .then((assignments) => setDirectReportIds(assignments.map((a) => a.principal_id)))
      .catch(() => setError(t("common.loadError")));
  }, [accessToken, user, t]);

  useEffect(reloadTasks, [reloadTasks]);

  const teamTasks =
    directReportIds === null
      ? []
      : tasks.filter(
          (task) =>
            (task.claimed_by !== null && directReportIds.includes(task.claimed_by)) ||
            (task.claimed_by === null && directReportIds.includes(task.created_by))
        );

  function openForm(taskId: string) {
    setOpenFormTaskId(taskId);
    setPrincipalIdInput("");
    setActionError(null);
  }

  async function handleAssign(task: ReadyTaskWithInstance, event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken || !principalIdInput.trim()) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await claimTask(accessToken, {
        instanceId: task.instance_id,
        taskId: task.id,
        principalId: principalIdInput.trim(),
      });
      setOpenFormTaskId(null);
      reloadTasks();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : t("teamTaskList.assignError"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReassign(task: ReadyTaskWithInstance, event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken || !principalIdInput.trim()) return;
    setSubmitting(true);
    setActionError(null);
    try {
      await reassignTask(accessToken, {
        instanceId: task.instance_id,
        taskId: task.id,
        newPrincipalId: principalIdInput.trim(),
      });
      setOpenFormTaskId(null);
      reloadTasks();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : t("teamTaskList.reassignError"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section>
      <h1>{t("teamTaskList.heading")}</h1>
      <p className="hint">{t("teamTaskList.hint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {actionError && (
        <p className="error-text" role="alert">
          {actionError}
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
              <th>{t("teamTaskList.statusColumn")}</th>
              <th></th>
              <th>{t("taskList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {teamTasks.map((task) => (
              <Fragment key={task.id}>
                <tr>
                  <td>{task.name}</td>
                  <td>#{task.process_definition_id}</td>
                  <td>{task.business_key ?? "-"}</td>
                  <td>
                    {task.claimed_by !== null
                      ? t("teamTaskList.statusClaimed", { principalId: task.claimed_by })
                      : t("teamTaskList.statusUnclaimed", { createdBy: task.created_by })}
                  </td>
                  <td>
                    <button type="button" onClick={() => onOpenInstance(task.instance_id)}>
                      {t("taskList.instanceLink")}
                    </button>
                  </td>
                  <td>
                    <button type="button" onClick={() => openForm(task.id)}>
                      {task.claimed_by === null
                        ? t("teamTaskList.assignButton")
                        : t("teamTaskList.reassignButton")}
                    </button>
                  </td>
                </tr>
                {openFormTaskId === task.id && (
                  <tr>
                    <td colSpan={6}>
                      <form
                        className="inline-form"
                        aria-label={
                          task.claimed_by === null
                            ? t("teamTaskList.assignFormLabel")
                            : t("teamTaskList.reassignFormLabel")
                        }
                        onSubmit={(event) =>
                          task.claimed_by === null
                            ? handleAssign(task, event)
                            : handleReassign(task, event)
                        }
                      >
                        <label>
                          {task.claimed_by === null
                            ? t("teamTaskList.assignPrincipalIdLabel")
                            : t("teamTaskList.reassignPrincipalIdLabel")}
                          <input
                            value={principalIdInput}
                            onChange={(e) => setPrincipalIdInput(e.target.value)}
                            required
                          />
                        </label>
                        <button type="submit" disabled={submitting}>
                          {task.claimed_by === null
                            ? t("teamTaskList.assignButton")
                            : t("teamTaskList.reassignButton")}
                        </button>
                      </form>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
