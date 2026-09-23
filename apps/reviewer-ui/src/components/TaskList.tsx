"use client";

import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  claimTask,
  completeTask,
  createTaskOrgHierarchyGrant,
  listActiveDelegationsForDeputy,
  listReadyTasks,
  releaseTaskClaim,
  type Delegation,
  type ReadyTaskWithInstance,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Approval task inbox (8, P14-S2) - consumes `GET /tasks` (workflow-service,
// new since this session), the first cross-instance task list in the entire
// system (previously only retrievable per instance individually via the
// Process Designer/curl, see docs/services/reviewer-ui.md).
export function TaskList({ onOpenInstance }: { onOpenInstance: (instanceId: string) => void }) {
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
  // Absence deputization (4.4a, P14-S11) - who the logged-in person is
  // currently actively registered as a deputy for, populates the "On behalf
  // of" selector below.
  const [delegations, setDelegations] = useState<Delegation[]>([]);

  // Task-claim & dynamic org-hierarchy access grants (post-roadmap phase 31
  // session 10) - claim/release act directly (no expand needed), the grant
  // form lives inside the existing expandable detail row, shown only once
  // the task is claimed by the logged-in person.
  const [claimError, setClaimError] = useState<string | null>(null);
  const [grantKind, setGrantKind] = useState<"supervisor" | "supervisor_chain" | "org_unit">(
    "supervisor"
  );
  const [grantOrgUnitOf, setGrantOrgUnitOf] = useState<"assignee" | "creator">("assignee");
  const [grantResultByTaskId, setGrantResultByTaskId] = useState<Record<string, string[]>>({});

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

  async function handleClaim(task: ReadyTaskWithInstance) {
    if (!accessToken) return;
    setClaimError(null);
    try {
      await claimTask(accessToken, {
        instanceId: task.instance_id,
        taskId: task.id,
        principalId: user?.username ?? "",
      });
      reload();
    } catch (err) {
      setClaimError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  async function handleReleaseClaim(task: ReadyTaskWithInstance) {
    if (!accessToken) return;
    setClaimError(null);
    try {
      await releaseTaskClaim(accessToken, { instanceId: task.instance_id, taskId: task.id });
      setGrantResultByTaskId((prev) => {
        const next = { ...prev };
        delete next[task.id];
        return next;
      });
      reload();
    } catch (err) {
      setClaimError(err instanceof ApiError ? err.message : t("common.actionError"));
    }
  }

  async function handleCreateGrant(task: ReadyTaskWithInstance, event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setClaimError(null);
    try {
      const result = await createTaskOrgHierarchyGrant(accessToken, {
        instanceId: task.instance_id,
        taskId: task.id,
        grantKind,
        orgUnitOf: grantKind === "org_unit" ? grantOrgUnitOf : undefined,
      });
      setGrantResultByTaskId((prev) => ({ ...prev, [task.id]: result.deputy_principal_ids }));
      reload();
    } catch (err) {
      setClaimError(err instanceof ApiError ? err.message : t("common.actionError"));
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
    <section>
      <h1>{t("taskList.heading")}</h1>
      <p className="text-sm opacity-80">{t("taskList.hint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {successMessage && <p className="text-success">{successMessage}</p>}
      {claimError && (
        <p className="text-danger" role="alert">
          {claimError}
        </p>
      )}

      {tasks.length === 0 ? (
        <p className="italic opacity-70">{t("taskList.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("taskList.nameColumn")}</th>
              <th className={th}>{t("taskList.processColumn")}</th>
              <th className={th}>{t("taskList.businessKeyColumn")}</th>
              <th className={th}>{t("taskList.laneColumn")}</th>
              <th className={th}>{t("taskList.claimColumn")}</th>
              <th className={th}></th>
              <th className={th}>{t("taskList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const isSignature = task.extensions.taskType === "signature";
              const isClaimedByMe = task.claimed_by !== null && task.claimed_by === user?.username;
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
                    <td className={td}>#{task.process_definition_id}</td>
                    <td className={td}>{task.business_key ?? "-"}</td>
                    <td className={td}>{task.lane ?? "-"}</td>
                    <td className={td}>
                      {/* Task-claim mechanism (post-roadmap phase 31 session
                          10) - the prerequisite for the org-hierarchy access
                          grant offered inside the expanded detail row below. */}
                      {task.claimed_by === null ? (
                        <button type="button" onClick={() => handleClaim(task)} className={secondaryBtn}>
                          {t("taskList.claimButton")}
                        </button>
                      ) : (
                        <>
                          <span>{t("taskList.claimedByLabel", { principalId: task.claimed_by })}</span>
                          {isClaimedByMe && (
                            <>
                              {" "}
                              <button
                                type="button"
                                onClick={() => handleReleaseClaim(task)}
                                className={secondaryBtn}
                              >
                                {t("taskList.releaseClaimButton")}
                              </button>
                            </>
                          )}
                        </>
                      )}
                    </td>
                    <td className={td}>
                      {/* Authenticated direct links (post-roadmap phase 29,
                          ADR 0109) - `instance_id` was already fetched for
                          `completeTask` above, but never shown/linked until
                          now. */}
                      <button
                        type="button"
                        onClick={() => onOpenInstance(task.instance_id)}
                        className={secondaryBtn}
                      >
                        {t("taskList.instanceLink")}
                      </button>
                    </td>
                    <td className={td}>
                      <button type="button" onClick={() => toggleExpand(task)} className={secondaryBtn}>
                        {t("taskList.completeButton")}
                      </button>
                    </td>
                  </tr>
                  {expandedTaskId === task.id && (
                    <tr className="bg-hover-bg">
                      <td colSpan={7} className="px-2 py-2">
                        {isClaimedByMe && (
                          <form
                            aria-label={t("taskList.grantFormLabel")}
                            className={inlineForm}
                            onSubmit={(event) => handleCreateGrant(task, event)}
                          >
                            <h2 className="text-sm opacity-80">{t("taskList.grantHeading")}</h2>
                            <label htmlFor={`grant-kind-${task.id}`} className={fieldLabel}>
                              {t("taskList.grantKindLabel")}
                            </label>
                            <select
                              id={`grant-kind-${task.id}`}
                              value={grantKind}
                              onChange={(e) =>
                                setGrantKind(
                                  e.target.value as "supervisor" | "supervisor_chain" | "org_unit"
                                )
                              }
                              className={fieldInput}
                            >
                              <option value="supervisor">{t("taskList.grantKindSupervisor")}</option>
                              <option value="supervisor_chain">
                                {t("taskList.grantKindSupervisorChain")}
                              </option>
                              <option value="org_unit">{t("taskList.grantKindOrgUnit")}</option>
                            </select>
                            {grantKind === "org_unit" && (
                              <>
                                <label htmlFor={`grant-org-unit-of-${task.id}`} className={fieldLabel}>
                                  {t("taskList.grantOrgUnitOfLabel")}
                                </label>
                                <select
                                  id={`grant-org-unit-of-${task.id}`}
                                  value={grantOrgUnitOf}
                                  onChange={(e) =>
                                    setGrantOrgUnitOf(e.target.value as "assignee" | "creator")
                                  }
                                  className={fieldInput}
                                >
                                  <option value="assignee">
                                    {t("taskList.grantOrgUnitOfAssignee")}
                                  </option>
                                  <option value="creator">
                                    {t("taskList.grantOrgUnitOfCreator")}
                                  </option>
                                </select>
                              </>
                            )}
                            <button type="submit" className={primaryBtn}>
                              {t("taskList.grantSubmit")}
                            </button>
                            {grantResultByTaskId[task.id] && (
                              <p className="text-sm opacity-80">
                                {grantResultByTaskId[task.id].length === 0
                                  ? t("taskList.grantEmptyResult")
                                  : t("taskList.grantResult", {
                                      deputies: grantResultByTaskId[task.id].join(", "),
                                    })}
                              </p>
                            )}
                          </form>
                        )}
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
                          {delegations.length > 0 && (
                            <>
                              <label htmlFor={`on-behalf-of-${task.id}`} className={fieldLabel}>
                                {t("taskList.onBehalfOfLabel")}
                              </label>
                              <select
                                id={`on-behalf-of-${task.id}`}
                                value={onBehalfOf}
                                onChange={(e) => setOnBehalfOf(e.target.value)}
                                className={fieldInput}
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
                              <p className="text-sm opacity-80">{t("taskList.signatureIdHint")}</p>
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
