"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteProcessDefinition,
  listProcessDefinitionVersions,
  listProcessDefinitions,
  restoreProcessDefinition,
  type ProcessDefinitionSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const CAN_MANAGE_CAPABILITY = "admin.object_config";

// Overview list of process definitions (P6-S8, concept 7.1). Shows only
// the latest version per process family (`name`) (`listProcessDefinitions`,
// filtered server-side via `DISTINCT ON`, see ADR 0027) - an
// expandable version history per row loads the full list of that
// family on demand.
export function ProcessDefinitionList() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const canManage = permissions.includes(CAN_MANAGE_CAPABILITY);

  const [definitions, setDefinitions] = useState<ProcessDefinitionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [history, setHistory] = useState<ProcessDefinitionSummary[]>([]);

  const reload = () => {
    if (!accessToken) return;
    listProcessDefinitions(accessToken)
      .then(setDefinitions)
      .catch(() => setError(t("common.loadError")));
  };

  useEffect(reload, [accessToken, t]);

  async function toggleHistory(name: string) {
    if (expandedName === name) {
      setExpandedName(null);
      return;
    }
    if (!accessToken) return;
    setExpandedName(name);
    try {
      setHistory(await listProcessDefinitionVersions(accessToken, name));
    } catch {
      setError(t("common.loadError"));
    }
  }

  async function handleDelete(id: number) {
    if (!accessToken) return;
    if (!window.confirm(t("processList.deleteConfirm"))) return;
    setError(null);
    try {
      await deleteProcessDefinition(accessToken, id);
      reload();
      if (expandedName) await toggleHistoryRefresh(expandedName);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  async function toggleHistoryRefresh(name: string) {
    if (!accessToken) return;
    try {
      setHistory(await listProcessDefinitionVersions(accessToken, name));
    } catch {
      // History stays at its previous state, not a blocker for the
      // actual delete confirmation above.
    }
  }

  // P25-S2/P71-S3 (7.1): reads an arbitrary historical version
  // (`process_definition_id` can be any version of the family) and
  // creates from it a brand-new, now-current version with identical
  // content - append-only like every other version, not an in-place
  // overwrite. No special "already the newest version" case: restoring
  // the current version simply creates another, content-identical one.
  async function handleRestore(id: number, name: string) {
    if (!accessToken) return;
    if (!window.confirm(t("processList.restoreConfirm"))) return;
    setError(null);
    try {
      await restoreProcessDefinition(accessToken, id);
      reload();
      await toggleHistoryRefresh(name);
      setExpandedName(name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("processList.restoreError"));
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";

  return (
    <section className="mx-auto max-w-[1100px] p-6">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <h1>{t("processList.heading")}</h1>
        <Link href="/designer/">
          <button type="button" className={secondaryBtn}>
            {t("processList.newButton")}
          </button>
        </Link>
      </div>

      {!canManage && <p className="text-sm opacity-80">{t("processList.noCapabilityHint")}</p>}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {definitions.length === 0 ? (
        <p className="italic opacity-70">{t("processList.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("processList.nameColumn")}</th>
              <th className={th}>{t("processList.versionColumn")}</th>
              <th className={th}>{t("processList.processIdColumn")}</th>
              <th className={th}>{t("processList.updatedColumn")}</th>
              <th className={th}>{t("processList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {definitions.map((definition) => (
              <Fragment key={definition.id}>
                <tr>
                  <td className={td}>{definition.name}</td>
                  <td className={td}>{definition.version}</td>
                  <td className={td}>{definition.bpmn_process_id}</td>
                  <td className={td}>{new Date(definition.updated_at).toLocaleString()}</td>
                  <td className={`${td} flex gap-2`}>
                    <Link href={`/designer/?id=${definition.id}`}>
                      <button type="button" className={secondaryBtn}>
                        {t("common.open")}
                      </button>
                    </Link>
                    <button
                      type="button"
                      onClick={() => toggleHistory(definition.name)}
                      className={secondaryBtn}
                    >
                      {expandedName === definition.name
                        ? t("processList.historyToggleHide")
                        : t("processList.historyToggleShow")}
                    </button>
                    {canManage && (
                      <button
                        type="button"
                        onClick={() => handleDelete(definition.id)}
                        className={secondaryBtn}
                      >
                        {t("common.delete")}
                      </button>
                    )}
                  </td>
                </tr>
                {expandedName === definition.name && (
                  <tr key={`${definition.id}-history`}>
                    <td colSpan={5} className="px-2 py-2">
                      <strong>{t("processList.history")}</strong>
                      <ul className="m-0 mt-2 list-none p-0">
                        {history.map((version) => (
                          <li
                            className="flex items-center justify-between gap-2 border-b border-border py-2"
                            key={version.id}
                          >
                            <span>
                              v{version.version} — {new Date(version.created_at).toLocaleString()}
                            </span>
                            <span className="flex gap-2">
                              <Link href={`/designer/?id=${version.id}`}>
                                <button type="button" className={secondaryBtn}>
                                  {t("common.open")}
                                </button>
                              </Link>
                              {canManage && version.id !== definition.id && (
                                <button
                                  type="button"
                                  onClick={() => handleRestore(version.id, version.name)}
                                  className={secondaryBtn}
                                >
                                  {t("processList.restoreButton")}
                                </button>
                              )}
                              {canManage && (
                                <button
                                  type="button"
                                  onClick={() => handleDelete(version.id)}
                                  className={secondaryBtn}
                                >
                                  {t("common.delete")}
                                </button>
                              )}
                            </span>
                          </li>
                        ))}
                      </ul>
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
