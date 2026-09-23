"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  deleteDmnDefinition,
  listDmnDefinitionVersions,
  listDmnDefinitions,
  listDmnReferences,
  type DmnDefinitionSummary,
  type ProcessDefinitionSummary,
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
  // P71-S3 (7.1): "which process definitions reference this DMN" cross-
  // reference view - a separate expand state from the version history
  // above (keyed by the latest version's `id`, since references are
  // checked against one specific DMN row's `decision_id`, not a family
  // name), so both can be open independently.
  const [expandedReferencesId, setExpandedReferencesId] = useState<number | null>(null);
  const [references, setReferences] = useState<ProcessDefinitionSummary[]>([]);

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

  async function toggleReferences(id: number) {
    if (expandedReferencesId === id) {
      setExpandedReferencesId(null);
      return;
    }
    if (!accessToken) return;
    setExpandedReferencesId(id);
    try {
      setReferences(await listDmnReferences(accessToken, id));
    } catch {
      setError(t("dmnList.referencesLoadError"));
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const th = "border-b border-border px-2 py-2 text-left";
  const td = "border-b border-border px-2 py-2 text-left";

  return (
    <section className="mx-auto max-w-[1100px] p-6">
      <div className="flex items-center justify-between border-b border-border px-6 py-3">
        <h1>{t("dmnList.heading")}</h1>
        <Link href="/dmn-designer/">
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
        <p className="italic opacity-70">{t("dmnList.empty")}</p>
      ) : (
        <table className="mb-6 w-full border-collapse">
          <thead>
            <tr>
              <th className={th}>{t("processList.nameColumn")}</th>
              <th className={th}>{t("processList.versionColumn")}</th>
              <th className={th}>{t("dmnList.decisionIdColumn")}</th>
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
                  <td className={td}>{definition.decision_id}</td>
                  <td className={td}>{new Date(definition.updated_at).toLocaleString()}</td>
                  <td className={`${td} flex flex-wrap gap-2`}>
                    <Link href={`/dmn-designer/?id=${definition.id}`}>
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
                    <button
                      type="button"
                      onClick={() => toggleReferences(definition.id)}
                      className={secondaryBtn}
                    >
                      {expandedReferencesId === definition.id
                        ? t("dmnList.referencesToggleHide")
                        : t("dmnList.referencesToggleShow")}
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
                {expandedReferencesId === definition.id && (
                  <tr key={`${definition.id}-references`}>
                    <td colSpan={5} className="px-2 py-2">
                      <strong>{t("dmnList.referencesHeading")}</strong>
                      {references.length === 0 ? (
                        <p className="italic opacity-70">{t("dmnList.referencesEmpty")}</p>
                      ) : (
                        <ul className="m-0 mt-2 list-none p-0">
                          {references.map((processDefinition) => (
                            <li
                              className="flex items-center justify-between gap-2 border-b border-border py-2"
                              key={processDefinition.id}
                            >
                              <span>
                                {processDefinition.name} (v{processDefinition.version})
                              </span>
                              <span className="flex gap-2">
                                <Link href={`/designer/?id=${processDefinition.id}`}>
                                  <button type="button" className={secondaryBtn}>
                                    {t("common.open")}
                                  </button>
                                </Link>
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                )}
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
                              <Link href={`/dmn-designer/?id=${version.id}`}>
                                <button type="button" className={secondaryBtn}>
                                  {t("common.open")}
                                </button>
                              </Link>
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
