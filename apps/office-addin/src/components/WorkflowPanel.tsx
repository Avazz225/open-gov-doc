"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  completeTask,
  listInstanceTasks,
  listInstancesForDocument,
  listProcessDefinitions,
  startInstance,
  type ProcessDefinition,
  type ProcessInstance,
  type ReadyTask,
} from "@/lib/api";

// "Start a workflow directly from the open document or
// continue/complete an open task on it" (3.3a). Correlation via
// `business_key = documentId` - the same mechanism already
// established throughout the system (no new endpoint/no new linking table
// needed, see `GET /instances?business_key=`).
export function WorkflowPanel({
  token,
  documentId,
  currentUsername,
}: {
  token: string;
  documentId: string;
  currentUsername: string;
}) {
  const { t } = useI18n();
  const [instances, setInstances] = useState<
    (ProcessInstance & { tasks: ReadyTask[] })[] | null
  >(null);
  const [definitions, setDefinitions] = useState<ProcessDefinition[]>([]);
  const [selectedDefinitionId, setSelectedDefinitionId] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const list = await listInstancesForDocument(token, documentId);
      const withTasks = await Promise.all(
        list
          .filter((instance) => instance.status === "running")
          .map(async (instance) => ({
            ...instance,
            tasks: await listInstanceTasks(token, instance.id),
          }))
      );
      setInstances(withTasks);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowPanel.loadError"));
      setInstances([]);
    }
  }, [token, documentId, t]);

  useEffect(() => {
    reload();
    listProcessDefinitions(token)
      .then(setDefinitions)
      .catch(() => setDefinitions([]));
  }, [reload, token]);

  async function handleStart(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedDefinitionId) return;
    setIsBusy(true);
    setError(null);
    try {
      await startInstance(token, Number(selectedDefinitionId), {
        createdBy: currentUsername,
        businessKey: documentId,
      });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowPanel.startError"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleComplete(instanceId: string, taskId: string) {
    setIsBusy(true);
    setError(null);
    try {
      await completeTask(token, { instanceId, taskId, completedBy: currentUsername });
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("workflowPanel.completeError"));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <section className="section" aria-label={t("workflowPanel.heading")}>
      <h2>{t("workflowPanel.heading")}</h2>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {instances === null ? (
        <p className="empty-state">{t("common.loading")}</p>
      ) : instances.length === 0 ? (
        <p className="empty-state">{t("workflowPanel.empty")}</p>
      ) : (
        <ul className="entry-list">
          {instances.map((instance) =>
            instance.tasks.length === 0 ? (
              <li className="entry-row" key={instance.id}>
                <span>{t("workflowPanel.runningNoTask", { id: instance.id.slice(0, 8) })}</span>
              </li>
            ) : (
              instance.tasks.map((task) => (
                <li className="entry-row" key={task.id}>
                  <span>{task.name}</span>
                  <button
                    type="button"
                    disabled={isBusy}
                    onClick={() => handleComplete(instance.id, task.id)}
                  >
                    {t("workflowPanel.completeButton")}
                  </button>
                </li>
              ))
            )
          )}
        </ul>
      )}
      {definitions.length > 0 && (
        <form onSubmit={handleStart}>
          <label htmlFor="ogdoc-process-definition">{t("workflowPanel.startLabel")}</label>
          <select
            id="ogdoc-process-definition"
            value={selectedDefinitionId}
            onChange={(e) => setSelectedDefinitionId(e.target.value)}
          >
            <option value="">{t("workflowPanel.selectPlaceholder")}</option>
            {definitions.map((def) => (
              <option key={def.id} value={def.id}>
                {def.name}
              </option>
            ))}
          </select>
          <button type="submit" disabled={isBusy || !selectedDefinitionId}>
            {t("workflowPanel.startButton")}
          </button>
        </form>
      )}
    </section>
  );
}
