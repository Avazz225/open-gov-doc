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

  return (
    <section aria-label={t("instanceDetail.paneLabel")}>
      <button type="button" onClick={onBack}>
        {t("instanceDetail.back")}
      </button>
      <h1>{t("instanceDetail.heading", { id: instanceId })}</h1>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {instance && (
        <dl className="detail-fields">
          <dt>{t("instanceDetail.statusLabel")}</dt>
          <dd>
            {instance.status === "running"
              ? t("instanceDetail.statusRunning")
              : t("instanceDetail.statusCompleted")}
          </dd>
          <dt>{t("instanceDetail.businessKeyLabel")}</dt>
          <dd>{instance.business_key ?? "-"}</dd>
          <dt>{t("instanceDetail.createdByLabel")}</dt>
          <dd>{instance.created_by}</dd>
          <dt>{t("instanceDetail.createdAtLabel")}</dt>
          <dd>{new Date(instance.created_at).toLocaleString()}</dd>
        </dl>
      )}

      <h2>{t("instanceDetail.tasksHeading")}</h2>
      <p className="hint">{t("instanceDetail.tasksHint")}</p>
      {successMessage && <p className="success-text">{successMessage}</p>}

      {tasks.length === 0 ? (
        <p className="empty-state">{t("taskList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("taskList.nameColumn")}</th>
              <th>{t("taskList.laneColumn")}</th>
              <th>{t("taskList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const isSignature = task.extensions.taskType === "signature";
              return (
                <Fragment key={task.id}>
                  <tr>
                    <td>
                      {task.name}
                      {isSignature && (
                        <>
                          {" "}
                          <span className="badge badge-pending">
                            {t("taskList.signatureBadge")}
                          </span>
                        </>
                      )}
                    </td>
                    <td>{task.lane ?? "-"}</td>
                    <td>
                      <button type="button" onClick={() => toggleExpand(task)}>
                        {t("taskList.completeButton")}
                      </button>
                    </td>
                  </tr>
                  {expandedTaskId === task.id && (
                    <tr className="detail-row">
                      <td colSpan={3}>
                        <form
                          className="inline-form"
                          onSubmit={(event) => handleComplete(task, event)}
                        >
                          <h2 className="hint">{t("taskList.completeHeading")}</h2>
                          <label htmlFor={`completed-by-${task.id}`}>
                            {t("taskList.completedByLabel")}
                          </label>
                          <input
                            id={`completed-by-${task.id}`}
                            value={completedBy}
                            onChange={(e) => setCompletedBy(e.target.value)}
                            required
                          />
                          {isSignature && (
                            <>
                              <label htmlFor={`signature-id-${task.id}`}>
                                {t("taskList.signatureIdLabel")}
                              </label>
                              <input
                                id={`signature-id-${task.id}`}
                                value={signatureId}
                                onChange={(e) => setSignatureId(e.target.value)}
                                required
                              />
                            </>
                          )}
                          <label htmlFor={`data-${task.id}`}>{t("taskList.dataLabel")}</label>
                          <textarea
                            id={`data-${task.id}`}
                            rows={3}
                            value={dataJson}
                            onChange={(e) => setDataJson(e.target.value)}
                            placeholder="{}"
                          />
                          {formError && (
                            <p className="error-text" role="alert">
                              {formError}
                            </p>
                          )}
                          <div className="actions">
                            <button type="submit" disabled={submitting}>
                              {submitting ? t("taskList.submitting") : t("taskList.submit")}
                            </button>
                            <button type="button" onClick={() => setExpandedTaskId(null)}>
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
