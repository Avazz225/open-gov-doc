"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getWorkflowInstance,
  getWorkflowProcessDefinition,
  listWorkflowCompletedTasks,
  listWorkflowInstances,
  listWorkflowInstanceTasks,
  listWorkflowProcessDefinitions,
  migrateWorkflowInstance,
  previewWorkflowMigration,
  sendBackWorkflowInstance,
  skipWorkflowTask,
  type InstanceModification,
  type MigrationReport,
  type WorkflowCompletedTask,
  type WorkflowInstance,
  type WorkflowProcessDefinition,
  type WorkflowReadyTask,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { PrincipalName } from "@/components/PrincipalName";

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

const MIN_REASON = 3;

// Running process instances and the three administrative interventions
// (P93-S2, ADR 0249 Part A c): skip a ready task, send the instance back to
// a completed task, migrate it to a newer version of its process. Master
// list on top, the selected instance's detail below. One reason field serves
// every action - it is mandatory and lands in the audit ledger.
export function WorkflowInstances() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [instances, setInstances] = useState<WorkflowInstance[]>([]);
  const [definitions, setDefinitions] = useState<Map<number, WorkflowProcessDefinition>>(new Map());
  // Typed value vs. the one applied by "Suchen".
  const [businessKey, setBusinessKey] = useState("");
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const [list, latest] = await Promise.all([
        listWorkflowInstances(accessToken, {
          status: "running",
          businessKey: query || undefined,
        }),
        listWorkflowProcessDefinitions(accessToken),
      ]);
      // The overview list holds only each family's newest version; instances
      // still on an older one are resolved individually.
      const byId = new Map(latest.map((d) => [d.id, d]));
      const missing = [...new Set(list.map((i) => i.process_definition_id))].filter(
        (id) => !byId.has(id)
      );
      const older = await Promise.all(
        missing.map((id) => getWorkflowProcessDefinition(accessToken, id).catch(() => null))
      );
      for (const def of older) if (def) byId.set(def.id, def);
      setInstances(list);
      setDefinitions(byId);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowInstances.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, query, t]);

  useEffect(() => {
    load();
  }, [load]);

  const selected = instances.find((i) => i.id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-6">
      <p className="m-0 text-sm opacity-80">{t("workflowInstances.intro")}</p>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          // Same query again still reloads (e.g. after someone else's change).
          if (businessKey.trim() === query) load();
          else setQuery(businessKey.trim());
        }}
      >
        <label className="flex flex-col gap-1 text-sm">
          {t("workflowInstances.businessKey")}
          <input
            className={fieldInput}
            value={businessKey}
            onChange={(e) => setBusinessKey(e.target.value)}
          />
        </label>
        <button type="submit" className={secondaryBtn}>
          {t("workflowInstances.search")}
        </button>
      </form>

      {error && (
        <p role="alert" className="m-0 text-sm text-danger">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : instances.length === 0 ? (
        <p className="italic opacity-70">{t("workflowInstances.empty")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("workflowInstances.businessKey")}</th>
                <th>{t("workflowInstances.process")}</th>
                <th>{t("workflowInstances.startedBy")}</th>
                <th>{t("workflowInstances.updatedAt")}</th>
              </tr>
            </thead>
            <tbody>
              {instances.map((instance) => {
                const def = definitions.get(instance.process_definition_id);
                const isSelected = instance.id === selectedId;
                return (
                  <tr
                    key={instance.id}
                    aria-selected={isSelected}
                    className={isSelected ? "bg-accent-bg" : undefined}
                  >
                    <td>
                      <button
                        type="button"
                        className="cursor-pointer border-0 bg-transparent p-0 text-left text-accent underline-offset-2 hover:underline"
                        onClick={() => setSelectedId(instance.id)}
                      >
                        {instance.business_key || instance.id.slice(0, 8)}
                      </button>
                    </td>
                    <td>{def ? `${def.name} v${def.version}` : instance.process_definition_id}</td>
                    <td>
                      <PrincipalName value={instance.created_by} />
                    </td>
                    <td>{new Date(instance.updated_at).toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <InstanceDetail
          key={selected.id}
          instance={selected}
          definition={definitions.get(selected.process_definition_id)}
          onChanged={(updated) =>
            setInstances((list) =>
              updated.status === "running"
                ? list.map((i) => (i.id === updated.id ? updated : i))
                : list.filter((i) => i.id !== updated.id)
            )
          }
        />
      )}
    </div>
  );
}

function InstanceDetail({
  instance: initial,
  definition: initialDefinition,
  onChanged,
}: {
  instance: WorkflowInstance;
  definition: WorkflowProcessDefinition | undefined;
  onChanged: (instance: WorkflowInstance) => void;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [instance, setInstance] = useState(initial);
  const [tasks, setTasks] = useState<WorkflowReadyTask[]>([]);
  const [completed, setCompleted] = useState<WorkflowCompletedTask[]>([]);
  const [reason, setReason] = useState("");
  const [sendBackTarget, setSendBackTarget] = useState("");
  const [migrationTarget, setMigrationTarget] = useState("");
  const [report, setReport] = useState<MigrationReport | null>(null);
  const [result, setResult] = useState<InstanceModification | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The process family's whole version history - migration targets come
  // from here, and after a migration the instance's new version too.
  const [family, setFamily] = useState<WorkflowProcessDefinition[]>([]);
  const familyName = initialDefinition?.name;
  useEffect(() => {
    if (!accessToken || !familyName) return;
    listWorkflowProcessDefinitions(accessToken, familyName)
      .then(setFamily)
      .catch(() => setFamily([]));
  }, [accessToken, familyName]);

  const definition =
    family.find((d) => d.id === instance.process_definition_id) ??
    (initialDefinition?.id === instance.process_definition_id ? initialDefinition : undefined);
  const newerVersions = useMemo(
    () =>
      family
        .filter((d) => definition && d.version > definition.version)
        .sort((a, b) => b.version - a.version),
    [family, definition]
  );
  const reasonOk = reason.trim().length >= MIN_REASON;

  const reload = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [fresh, ready, done] = await Promise.all([
        getWorkflowInstance(accessToken, instance.id),
        listWorkflowInstanceTasks(accessToken, instance.id),
        listWorkflowCompletedTasks(accessToken, instance.id),
      ]);
      setInstance(fresh);
      setTasks(ready);
      setCompleted(done);
      setSendBackTarget((current) =>
        done.some((c) => c.id === current) ? current : (done[done.length - 1]?.id ?? "")
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowInstances.loadError"));
    }
  }, [accessToken, instance.id, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    setMigrationTarget(
      (current) => current || (newerVersions[0] ? String(newerVersions[0].id) : "")
    );
  }, [newerVersions]);

  async function run(action: () => Promise<InstanceModification>) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const outcome = await action();
      setResult(outcome);
      setReason("");
      setReport(null);
      setInstance(outcome.instance);
      onChanged(outcome.instance);
      if (outcome.instance.status === "running") await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowInstances.actionError"));
      // A conflict means someone else moved the instance - show the new state.
      if (err instanceof ApiError && err.status === 409) await reload();
    } finally {
      setBusy(false);
    }
  }

  async function preview() {
    if (!accessToken || !migrationTarget) return;
    setBusy(true);
    setError(null);
    try {
      setReport(await previewWorkflowMigration(accessToken, instance.id, Number(migrationTarget)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowInstances.actionError"));
    } finally {
      setBusy(false);
    }
  }

  const token = accessToken ?? "";
  const version = instance.workflow_version;

  return (
    <section
      aria-label={t("workflowInstances.detailHeading")}
      className="flex flex-col gap-5 rounded-lg border border-border p-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="m-0 text-lg">
          {instance.business_key || instance.id.slice(0, 8)}
          <span className="ml-2 text-sm font-normal opacity-70">
            {definition ? `${definition.name} v${definition.version}` : ""}
          </span>
        </h2>
        <code className="text-xs opacity-60">{instance.id}</code>
      </header>

      <div className="flex flex-col gap-1 text-sm">
        <label className="flex flex-col gap-1">
          {t("workflowInstances.reason")}
          <textarea
            className={`${fieldInput} min-h-16`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t("workflowInstances.reasonPlaceholder")}
            aria-describedby="workflow-instance-reason-hint"
          />
        </label>
        <span id="workflow-instance-reason-hint" className="text-xs opacity-70">
          {t("workflowInstances.reasonHint")}
        </span>
      </div>

      {error && (
        <p role="alert" className="m-0 text-sm text-danger">
          {error}
        </p>
      )}
      {result && (
        <p role="status" className="m-0 rounded-md bg-accent-bg px-3 py-2 text-sm">
          {t(`workflowInstances.done.${result.operation}`, {
            target: result.target,
          })}{" "}
          {result.instance.status === "completed"
            ? t("workflowInstances.nowCompleted")
            : t("workflowInstances.nowOpen", {
                tasks: result.tasks_after.join(", ") || "—",
              })}
          {result.released_claims.length > 0 && (
            <>
              {" "}
              {t("workflowInstances.claimsReleased", {
                count: String(result.released_claims.length),
              })}
            </>
          )}
        </p>
      )}

      {instance.status !== "running" ? (
        <p className="m-0 italic opacity-70">{t("workflowInstances.notRunning")}</p>
      ) : (
        <>
          <div className="flex flex-col gap-2">
            <h3 className="m-0 text-base">{t("workflowInstances.openTasks")}</h3>
            {tasks.length === 0 ? (
              <p className="m-0 text-sm italic opacity-70">{t("workflowInstances.noOpenTasks")}</p>
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {tasks.map((task) => (
                  <li
                    key={task.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
                  >
                    <span className="flex flex-col">
                      <span>{task.name}</span>
                      <span className="text-xs opacity-70">
                        {task.lane ? `${t("workflowInstances.lane")}: ${task.lane} · ` : ""}
                        {task.claimed_by ? (
                          <>
                            {t("workflowInstances.claimedBy")}{" "}
                            <PrincipalName value={task.claimed_by} />
                          </>
                        ) : (
                          t("workflowInstances.unclaimed")
                        )}
                      </span>
                    </span>
                    <button
                      type="button"
                      className={secondaryBtn}
                      disabled={!reasonOk || busy}
                      onClick={() =>
                        run(() => skipWorkflowTask(token, instance.id, task.id, reason, version))
                      }
                    >
                      {t("workflowInstances.skip")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <h3 className="m-0 text-base">{t("workflowInstances.sendBackHeading")}</h3>
            {completed.length === 0 ? (
              <p className="m-0 text-sm italic opacity-70">{t("workflowInstances.noCompleted")}</p>
            ) : (
              <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-sm">
                  {t("workflowInstances.sendBackTarget")}
                  <select
                    className={fieldInput}
                    value={sendBackTarget}
                    onChange={(e) => setSendBackTarget(e.target.value)}
                  >
                    {completed.map((task) => (
                      <option key={task.id} value={task.id}>
                        {task.name}
                        {task.completed_at
                          ? ` (${new Date(task.completed_at).toLocaleString()})`
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className={secondaryBtn}
                  disabled={!reasonOk || busy || !sendBackTarget}
                  onClick={() =>
                    run(() =>
                      sendBackWorkflowInstance(token, instance.id, sendBackTarget, reason, version)
                    )
                  }
                >
                  {t("workflowInstances.sendBack")}
                </button>
              </div>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <h3 className="m-0 text-base">{t("workflowInstances.migrateHeading")}</h3>
            {newerVersions.length === 0 ? (
              <p className="m-0 text-sm italic opacity-70">
                {t("workflowInstances.noNewerVersion")}
              </p>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1 text-sm">
                    {t("workflowInstances.migrationTarget")}
                    <select
                      className={fieldInput}
                      value={migrationTarget}
                      onChange={(e) => {
                        setMigrationTarget(e.target.value);
                        setReport(null);
                      }}
                    >
                      {newerVersions.map((d) => (
                        <option key={d.id} value={d.id}>
                          {`${d.name} v${d.version}`}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button type="button" className={secondaryBtn} disabled={busy} onClick={preview}>
                    {t("workflowInstances.preview")}
                  </button>
                  <button
                    type="button"
                    className={primaryBtn}
                    disabled={!reasonOk || busy || !report?.possible}
                    onClick={() =>
                      run(() =>
                        migrateWorkflowInstance(
                          token,
                          instance.id,
                          Number(migrationTarget),
                          reason,
                          version
                        )
                      )
                    }
                  >
                    {t("workflowInstances.migrate")}
                  </button>
                </div>
                {report && <MigrationReportView report={report} />}
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function MigrationReportView({ report }: { report: MigrationReport }) {
  const { t } = useI18n();
  const groups: { key: "added" | "removed" | "changed"; items: string[] }[] = [
    { key: "added", items: report.added },
    { key: "removed", items: report.removed },
    { key: "changed", items: report.changed },
  ];
  return (
    <div
      aria-label={t("workflowInstances.reportHeading")}
      className="flex flex-col gap-3 rounded-md border border-border p-3 text-sm"
    >
      <p
        role="status"
        className={`m-0 font-medium ${report.possible ? "text-success" : "text-danger"}`}
      >
        {report.possible
          ? t("workflowInstances.reportPossible")
          : t("workflowInstances.reportImpossible")}
      </p>
      {report.reasons.length > 0 && (
        <ul className="m-0 pl-5">
          {report.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1">
        {groups.map((group) => (
          <div key={group.key} className="contents">
            <dt className="opacity-70">{t(`workflowInstances.diff.${group.key}`)}</dt>
            <dd className="m-0">{group.items.length ? group.items.join(", ") : "—"}</dd>
          </div>
        ))}
      </dl>
      {report.active_tasks.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("workflowInstances.activeTask")}</th>
              <th>{t("workflowInstances.alignedTo")}</th>
            </tr>
          </thead>
          <tbody>
            {report.active_tasks.map((task) => (
              <tr key={task.name}>
                <td>{task.name}</td>
                <td className={task.aligned_to ? undefined : "text-danger"}>
                  {task.aligned_to ?? t("workflowInstances.noCounterpart")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
