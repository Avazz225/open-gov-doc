"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getConformityReport,
  listObjectTypes,
  retypeObjects,
  type ConformityReport,
  type ObjectType,
  type RetypeResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// What the current rules say about existing folders or documents (P91-S3,
// ADR 0255 Decision 4): placement on both sides, missing required
// attributes, deleted types - whatever the enforcement mode. Selected rows
// can be retyped in bulk; the server checks each one and lists failures.
export function ConformityReportPanel({
  kind,
  refreshKey,
}: {
  kind: "folders" | "documents";
  refreshKey?: number;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [report, setReport] = useState<ConformityReport | null>(null);
  const [types, setTypes] = useState<ObjectType[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [targetType, setTargetType] = useState("");
  const [result, setResult] = useState<RetypeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setLoading(true);
    setError(null);
    try {
      const [loaded, objectTypes] = await Promise.all([
        getConformityReport(accessToken, kind),
        listObjectTypes(accessToken),
      ]);
      setReport(loaded);
      setTypes(objectTypes.filter((ot) => ot.applies_to === (kind === "folders" ? "folder" : "document")));
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setLoading(false);
    }
  }, [accessToken, kind, t]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function retype() {
    if (!accessToken || selected.size === 0 || !targetType) return;
    setError(null);
    try {
      const outcome = await retypeObjects(
        accessToken,
        kind,
        [...selected],
        targetType === "none" ? null : Number(targetType)
      );
      setResult(outcome);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("structure.retypeError"));
    }
  }

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const heading = t(kind === "folders" ? "structure.reportFolders" : "structure.reportDocuments");
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent";
  const button =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

  return (
    <section className="mb-6 min-w-0" aria-label={heading}>
      <div className="flex flex-wrap items-baseline gap-3">
        <h3 className="m-0">{heading}</h3>
        <button type="button" className={button} onClick={load} disabled={loading}>
          {t("structure.reload")}
        </button>
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {report && (
        <>
          <p className="text-sm" role="status">
            {report.nonconforming === 0
              ? t("structure.reportClean", { checked: report.checked })
              : t("structure.reportSummary", {
                  checked: report.checked,
                  nonconforming: report.nonconforming,
                  placement: report.counts.placement,
                  missing: report.counts.missing_required,
                  unknown: report.counts.unknown_type,
                })}
            {report.truncated && ` ${t("structure.reportTruncated", { shown: report.items.length })}`}
          </p>
          {report.items.length > 0 && (
            <>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-sm">
                  {t("structure.retypeTo")}
                  <select
                    value={targetType}
                    onChange={(e) => setTargetType(e.target.value)}
                    className={`${fieldInput} max-w-[16rem]`}
                  >
                    <option value="">{t("structure.chooseType")}</option>
                    {types.map((ot) => (
                      <option key={ot.id} value={ot.id}>
                        {ot.name}
                      </option>
                    ))}
                    <option value="none">{t("structure.removeType")}</option>
                  </select>
                </label>
                <button
                  type="button"
                  className={button}
                  disabled={selected.size === 0 || !targetType}
                  onClick={retype}
                >
                  {t("structure.retypeSelected", { count: selected.size })}
                </button>
              </div>
              {result && (
                <div className="mt-2 text-sm" role="status" aria-label={t("structure.retypeResult")}>
                  {t("structure.retypeDone", { updated: result.updated.length, failed: result.failed.length })}
                  {result.failed.length > 0 && (
                    <ul className="mb-0 mt-1">
                      {result.failed.map((f) => (
                        <li key={f.id}>
                          <code>{f.id}</code>: {f.errors.join(" ")}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <div className="dms-scroll-x mt-2 max-h-[28rem] overflow-y-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>
                        <span className="sr-only">{t("structure.select")}</span>
                      </th>
                      <th>{t(kind === "folders" ? "structure.colPath" : "structure.colTitle")}</th>
                      <th>{t("structure.colType")}</th>
                      <th>{t("structure.colProblems")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.items.map((item) => {
                      const label = item.path ?? item.title ?? item.id;
                      return (
                        <tr key={item.id}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={t("structure.selectItem", { name: label })}
                              checked={selected.has(item.id)}
                              onChange={() => toggle(item.id)}
                            />
                          </td>
                          <td className="min-w-[9rem] [overflow-wrap:anywhere]">{label}</td>
                          <td>{item.object_type ?? "—"}</td>
                          <td>
                            {item.problems.map((p, i) => (
                              <div key={i}>
                                <strong>{t(`structure.problem.${p.kind}`)}:</strong> {p.messages.join(" ")}
                              </div>
                            ))}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
