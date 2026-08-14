"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteDmnDefinition,
  listDmnDefinitionVersions,
  listDmnDefinitions,
  type DmnDefinitionSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const CAN_MANAGE_CAPABILITY = "admin.object_config";

// Overview list of DMN 1.3 decision tables (7.1, P14-S4) - an exact
// analogue to `ProcessDefinitionList.tsx` (same versioning pattern:
// `name` is the family key, only the respective latest version is
// shown by default).
export function DmnDefinitionList() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const canManage = permissions.includes(CAN_MANAGE_CAPABILITY);

  const [definitions, setDefinitions] = useState<DmnDefinitionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [history, setHistory] = useState<DmnDefinitionSummary[]>([]);

  const reload = () => {
    if (!accessToken) return;
    listDmnDefinitions(accessToken)
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
      setHistory(await listDmnDefinitionVersions(accessToken, name));
    } catch {
      setError(t("common.loadError"));
    }
  }

  async function handleDelete(id: number) {
    if (!accessToken) return;
    if (!window.confirm(t("dmnList.deleteConfirm"))) return;
    setError(null);
    try {
      await deleteDmnDefinition(accessToken, id);
      reload();
      if (expandedName) await toggleHistoryRefresh(expandedName);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  async function toggleHistoryRefresh(name: string) {
    if (!accessToken) return;
    try {
      setHistory(await listDmnDefinitionVersions(accessToken, name));
    } catch {
      // History stays at its previous state, not a blocker for the
      // actual delete confirmation above.
    }
  }

  return (
    <section className="page">
      <div className="top-bar">
        <h1>{t("dmnList.heading")}</h1>
        <Link href="/dmn-designer/">
          <button type="button">{t("processList.newButton")}</button>
        </Link>
      </div>

      {!canManage && <p className="hint">{t("processList.noCapabilityHint")}</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {definitions.length === 0 ? (
        <p className="empty-state">{t("dmnList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processList.nameColumn")}</th>
              <th>{t("processList.versionColumn")}</th>
              <th>{t("dmnList.decisionIdColumn")}</th>
              <th>{t("processList.updatedColumn")}</th>
              <th>{t("processList.actionsColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {definitions.map((definition) => (
              <Fragment key={definition.id}>
                <tr>
                  <td>{definition.name}</td>
                  <td>{definition.version}</td>
                  <td>{definition.decision_id}</td>
                  <td>{new Date(definition.updated_at).toLocaleString()}</td>
                  <td className="actions">
                    <Link href={`/dmn-designer/?id=${definition.id}`}>
                      <button type="button">{t("common.open")}</button>
                    </Link>
                    <button type="button" onClick={() => toggleHistory(definition.name)}>
                      {expandedName === definition.name
                        ? t("processList.historyToggleHide")
                        : t("processList.historyToggleShow")}
                    </button>
                    {canManage && (
                      <button type="button" onClick={() => handleDelete(definition.id)}>
                        {t("common.delete")}
                      </button>
                    )}
                  </td>
                </tr>
                {expandedName === definition.name && (
                  <tr key={`${definition.id}-history`}>
                    <td colSpan={5}>
                      <strong>{t("processList.history")}</strong>
                      <ul className="entry-list">
                        {history.map((version) => (
                          <li className="entry-row" key={version.id}>
                            <span>
                              v{version.version} — {new Date(version.created_at).toLocaleString()}
                            </span>
                            <span className="actions">
                              <Link href={`/dmn-designer/?id=${version.id}`}>
                                <button type="button">{t("common.open")}</button>
                              </Link>
                              {canManage && (
                                <button type="button" onClick={() => handleDelete(version.id)}>
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
