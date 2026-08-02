"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteProcessDefinition,
  listProcessDefinitionVersions,
  listProcessDefinitions,
  type ProcessDefinitionSummary,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const CAN_MANAGE_CAPABILITY = "admin.object_config";

// Übersichtsliste der Prozessdefinitionen (P6-S8, Konzept 7.1). Zeigt je
// Prozessfamilie (`name`) nur die neueste Version (`listProcessDefinitions`,
// serverseitig per `DISTINCT ON` gefiltert, siehe ADR 0027) - eine
// aufklappbare Versionshistorie je Zeile lädt bei Bedarf die vollständige
// Liste dieser Familie nach.
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
      // Historie bleibt beim vorherigen Stand, kein Blocker für die
      // eigentliche Löschbestätigung oben.
    }
  }

  return (
    <section className="page">
      <div className="top-bar">
        <h1>{t("processList.heading")}</h1>
        <Link href="/designer/">
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
        <p className="empty-state">{t("processList.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("processList.nameColumn")}</th>
              <th>{t("processList.versionColumn")}</th>
              <th>{t("processList.processIdColumn")}</th>
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
                  <td>{definition.bpmn_process_id}</td>
                  <td>{new Date(definition.updated_at).toLocaleString()}</td>
                  <td className="actions">
                    <Link href={`/designer/?id=${definition.id}`}>
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
                              <Link href={`/designer/?id=${version.id}`}>
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
