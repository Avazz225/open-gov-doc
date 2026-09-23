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

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";

  return (
    <section>
      <h1>{t("teamTaskList.heading")}</h1>
      <p className="text-sm opacity-80">{t("teamTaskList.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {actionError && (
        <p className="text-danger" role="alert">
          {actionError}
        </p>
      )}

      {directReportIds !== null && directReportIds.length === 0 ? (
        <p className="italic opacity-70">{t("teamTaskList.noDirectReports")}</p>
      ) : teamTasks.length === 0 ? (
        <p className="italic opacity-70">{t("teamTaskList.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("taskList.nameColumn")}</th>
              <th className={th}>{t("taskList.processColumn")}</th>
              <th className={th}>{t("taskList.businessKeyColumn")}</th>
              <th className={th}>{t("teamTaskList.statusColumn")}</th>
              <th className={th}></th>
              <th className={th}>{t("taskList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {teamTasks.map((task) => (
              <Fragment key={task.id}>
                <tr>
                  <td className={td}>{task.name}</td>
                  <td className={td}>#{task.process_definition_id}</td>
                  <td className={td}>{task.business_key ?? "-"}</td>
                  <td className={td}>
                    {task.claimed_by !== null
                      ? t("teamTaskList.statusClaimed", { principalId: task.claimed_by })
                      : t("teamTaskList.statusUnclaimed", { createdBy: task.created_by })}
                  </td>
                  <td className={td}>
                    <button
                      type="button"
                      onClick={() => onOpenInstance(task.instance_id)}
                      className={secondaryBtn}
                    >
                      {t("taskList.instanceLink")}
                    </button>
                  </td>
                  <td className={td}>
                    <button type="button" onClick={() => openForm(task.id)} className={secondaryBtn}>
                      {task.claimed_by === null
                        ? t("teamTaskList.assignButton")
                        : t("teamTaskList.reassignButton")}
                    </button>
                  </td>
                </tr>
                {openFormTaskId === task.id && (
                  <tr>
                    <td colSpan={6} className="px-2 py-2">
                      <form
                        className="mt-2 flex max-w-[420px] flex-col gap-2 rounded-sm border border-border p-3"
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
                        <label className="flex flex-col gap-1 text-sm font-medium text-fg">
                          {task.claimed_by === null
                            ? t("teamTaskList.assignPrincipalIdLabel")
                            : t("teamTaskList.reassignPrincipalIdLabel")}
                          <input
                            value={principalIdInput}
                            onChange={(e) => setPrincipalIdInput(e.target.value)}
                            required
                            className="box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
                          />
                        </label>
                        <button type="submit" disabled={submitting} className={primaryBtn}>
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
