"use client";

import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeTask,
  listActiveDelegationsForDeputy,
  listReadyTasks,
  type Delegation,
  type ReadyTaskWithInstance,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Freigabeaufgaben-Inbox (8, P14-S2) - konsumiert `GET /tasks`
// (workflow-service, neu seit dieser Session), die erste Cross-Instanz-
// Aufgabenliste im gesamten System (vorher nur je Instanz einzeln über den
// Process Designer/curl abrufbar, siehe docs/services/reviewer-ui.md).
export function TaskList() {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [tasks, setTasks] = useState<ReadyTaskWithInstance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [completedBy, setCompletedBy] = useState("");
  const [signatureId, setSignatureId] = useState("");
  const [dataJson, setDataJson] = useState("");
  const [onBehalfOf, setOnBehalfOf] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  // Stellvertretung bei Abwesenheit (4.4a, P14-S11) - für wen die
  // angemeldete Person gerade aktiv als Stellvertretung eingetragen ist,
  // füllt die "Im Auftrag von"-Auswahl unten.
  const [delegations, setDelegations] = useState<Delegation[]>([]);

  const reload = () => {
    if (!accessToken) return;
    listReadyTasks(accessToken)
      .then(setTasks)
      .catch(() => setError(t("common.loadError")));
  };

  useEffect(reload, [accessToken, t]);

  useEffect(() => {
    if (!accessToken || !user) return;
    listActiveDelegationsForDeputy(accessToken, user.sub)
      .then(setDelegations)
      .catch(() => setDelegations([]));
  }, [accessToken, user]);

  function toggleExpand(task: ReadyTaskWithInstance) {
    if (expandedTaskId === task.id) {
      setExpandedTaskId(null);
      return;
    }
    setExpandedTaskId(task.id);
    setCompletedBy(user?.username ?? "");
    setSignatureId("");
    setDataJson("");
    setOnBehalfOf("");
    setFormError(null);
    setSuccessMessage(null);
  }

  async function handleComplete(task: ReadyTaskWithInstance, event: React.FormEvent) {
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
        instanceId: task.instance_id,
        taskId: task.id,
        completedBy,
        data,
        signatureId: signatureId || undefined,
        onBehalfOfPrincipalId: onBehalfOf || undefined,
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
    <section>
      <h1>{t("taskList.heading")}</h1>
      <p className="hint">{t("taskList.hint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {successMessage && <p className="success-text">{successMessage}</p>}

      {tasks.length === 0 ? (
        <p className="empty-state">{t("taskList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("taskList.nameColumn")}</th>
              <th>{t("taskList.processColumn")}</th>
              <th>{t("taskList.businessKeyColumn")}</th>
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
                    <td>#{task.process_definition_id}</td>
                    <td>{task.business_key ?? "-"}</td>
                    <td>{task.lane ?? "-"}</td>
                    <td>
                      <button type="button" onClick={() => toggleExpand(task)}>
                        {t("taskList.completeButton")}
                      </button>
                    </td>
                  </tr>
                  {expandedTaskId === task.id && (
                    <tr className="detail-row">
                      <td colSpan={5}>
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
                          {delegations.length > 0 && (
                            <>
                              <label htmlFor={`on-behalf-of-${task.id}`}>
                                {t("taskList.onBehalfOfLabel")}
                              </label>
                              <select
                                id={`on-behalf-of-${task.id}`}
                                value={onBehalfOf}
                                onChange={(e) => setOnBehalfOf(e.target.value)}
                              >
                                <option value="">{t("taskList.onBehalfOfSelf")}</option>
                                {delegations.map((delegation) => (
                                  <option
                                    key={delegation.id}
                                    value={delegation.delegator_principal_id}
                                  >
                                    {delegation.delegator_principal_id}
                                  </option>
                                ))}
                              </select>
                            </>
                          )}
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
                              <p className="hint">{t("taskList.signatureIdHint")}</p>
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
