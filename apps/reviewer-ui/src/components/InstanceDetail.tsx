"use client";

import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeTask,
  getInstance,
  listInstanceTasks,
  type ProcessInstance,
  type ReadyTask,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// "Vorgang" direct-link detail view (post-roadmap phase 29, ADR 0109/0110) -
// the first UI anywhere addressing a single process instance by ID
// (`?instance=<id>`, see page.tsx). Deliberately shows only status and
// currently-open tasks, no task history - workflow-service persists none
// (opaque `workflow_state`, ADR 0019), so a history section would have to
// be invented rather than genuinely sourced.
export function InstanceDetail({
  instanceId,
  onBack,
}: {
  instanceId: string;
  onBack: () => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [instance, setInstance] = useState<ProcessInstance | null>(null);
  const [tasks, setTasks] = useState<ReadyTask[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [completedBy, setCompletedBy] = useState("");
  const [signatureId, setSignatureId] = useState("");
  const [dataJson, setDataJson] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const reload = () => {
    if (!accessToken) return;
    setError(null);
    Promise.all([getInstance(accessToken, instanceId), listInstanceTasks(accessToken, instanceId)])
      .then(([loadedInstance, loadedTasks]) => {
        setInstance(loadedInstance);
        setTasks(loadedTasks);
      })
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : t("instanceDetail.loadError"))
      );
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(reload, [accessToken, instanceId]);

  function toggleExpand(task: ReadyTask) {
    if (expandedTaskId === task.id) {
      setExpandedTaskId(null);
      return;
    }
    setExpandedTaskId(task.id);
    setCompletedBy(user?.username ?? "");
    setSignatureId("");
    setDataJson("");
    setFormError(null);
    setSuccessMessage(null);
  }

  async function handleComplete(task: ReadyTask, event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setFormError(null);
    let data: Record<string, unknown> = {};
    if (dataJson.trim()) {
      try {
        data = JSON.parse(dataJson);
      } catch {
        setFormError(t("taskList.dataInvalid"));
        return;
      }
    }
    setSubmitting(true);
    try {
      await completeTask(accessToken, {
        instanceId,
        taskId: task.id,
        completedBy,
        data,
        signatureId: signatureId || undefined,
      });
      setSuccessMessage(t("taskList.success"));
      setExpandedTaskId(null);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setSubmitting(false);
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const fieldLabel = "text-sm font-medium text-fg";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";
  const badge = "badge-hc-border inline-block rounded-full bg-accent-bg px-2 py-[0.1rem] text-xs text-accent";
  const inlineForm = "mt-2 flex max-w-[420px] flex-col gap-2 rounded-sm border border-border p-3";

  return (
    <section aria-label={t("instanceDetail.paneLabel")}>
      <button type="button" onClick={onBack} className={secondaryBtn}>
        {t("instanceDetail.back")}
      </button>
      <h1>{t("instanceDetail.heading", { id: instanceId })}</h1>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {instance && (
        <dl className="my-4 mb-6 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
          <dt className="font-semibold opacity-80">{t("instanceDetail.statusLabel")}</dt>
          <dd className="m-0">
            {instance.status === "running"
              ? t("instanceDetail.statusRunning")
              : t("instanceDetail.statusCompleted")}
          </dd>
          <dt className="font-semibold opacity-80">{t("instanceDetail.businessKeyLabel")}</dt>
          <dd className="m-0">{instance.business_key ?? "-"}</dd>
          <dt className="font-semibold opacity-80">{t("instanceDetail.createdByLabel")}</dt>
          <dd className="m-0">{instance.created_by}</dd>
          <dt className="font-semibold opacity-80">{t("instanceDetail.createdAtLabel")}</dt>
          <dd className="m-0">{new Date(instance.created_at).toLocaleString()}</dd>
        </dl>
      )}

      <h2>{t("instanceDetail.tasksHeading")}</h2>
      <p className="text-sm opacity-80">{t("instanceDetail.tasksHint")}</p>
      {successMessage && <p className="text-success">{successMessage}</p>}

      {tasks.length === 0 ? (
        <p className="italic opacity-70">{t("taskList.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("taskList.nameColumn")}</th>
              <th className={th}>{t("taskList.laneColumn")}</th>
              <th className={th}>{t("taskList.claimColumn")}</th>
              <th className={th}>{t("taskList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const isSignature = task.extensions.taskType === "signature";
              return (
                <Fragment key={task.id}>
                  <tr>
                    <td className={td}>
                      {task.name}
                      {isSignature && (
                        <>
                          {" "}
                          <span className={badge}>{t("taskList.signatureBadge")}</span>
                        </>
                      )}
                    </td>
                    <td className={td}>{task.lane ?? "-"}</td>
                    {/* Read-only awareness only (post-roadmap phase 31 session
                        10) - claiming/org-hierarchy grants stay a TaskList.tsx
                        action, this view deliberately stays a lightweight
                        status display (ADR 0110), same precedent as omitting
                        the "on behalf of" delegation selector below. */}
                    <td className={td}>{task.claimed_by ?? "-"}</td>
                    <td className={td}>
                      <button type="button" onClick={() => toggleExpand(task)} className={secondaryBtn}>
                        {t("taskList.completeButton")}
                      </button>
                    </td>
                  </tr>
                  {expandedTaskId === task.id && (
                    <tr className="bg-hover-bg">
                      <td colSpan={4} className="px-2 py-2">
                        <form className={inlineForm} onSubmit={(event) => handleComplete(task, event)}>
                          <h2 className="text-sm opacity-80">{t("taskList.completeHeading")}</h2>
                          <label htmlFor={`completed-by-${task.id}`} className={fieldLabel}>
                            {t("taskList.completedByLabel")}
                          </label>
                          <input
                            id={`completed-by-${task.id}`}
                            value={completedBy}
                            onChange={(e) => setCompletedBy(e.target.value)}
                            required
                            className={fieldInput}
                          />
                          {isSignature && (
                            <>
                              <label htmlFor={`signature-id-${task.id}`} className={fieldLabel}>
                                {t("taskList.signatureIdLabel")}
                              </label>
                              <input
                                id={`signature-id-${task.id}`}
                                value={signatureId}
                                onChange={(e) => setSignatureId(e.target.value)}
                                required
                                className={fieldInput}
                              />
                            </>
                          )}
                          <label htmlFor={`data-${task.id}`} className={fieldLabel}>
                            {t("taskList.dataLabel")}
                          </label>
                          <textarea
                            id={`data-${task.id}`}
                            rows={3}
                            value={dataJson}
                            onChange={(e) => setDataJson(e.target.value)}
                            placeholder="{}"
                            className={fieldInput}
                          />
                          {formError && (
                            <p className="text-danger" role="alert">
                              {formError}
                            </p>
                          )}
                          <div className="flex gap-2">
                            <button type="submit" disabled={submitting} className={primaryBtn}>
                              {submitting ? t("taskList.submitting") : t("taskList.submit")}
                            </button>
                            <button
                              type="button"
                              onClick={() => setExpandedTaskId(null)}
                              className={secondaryBtn}
                            >
                              {t("common.cancel")}
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
