"use client";

import { useCallback, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  exportForensicTrace,
  getForensicTrace,
  type ForensicTraceCategory,
  type ForensicTraceEntry,
  type ForensicTraceFilters,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Forensik-Trace (5.4b, seit P7-S2c) - objektbezogene Nachverfolgung
// ("alle Aktionen von Nutzer X"/"alle Nutzer auf Dokument Y"), zentraler
// Anwendungsfall: kompromittierter Account. Jede Abfrage wird selbst wieder
// auditiert (reporting.forensic_trace.queried, seit dieser Session), daher
// braucht jeder Aufruf den aktuell angemeldeten Principal als `queried_by`.
export function ForensicTraceView() {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();

  const [actor, setActor] = useState("");
  const [subject, setSubject] = useState("");
  const [category, setCategory] = useState<ForensicTraceCategory | "">("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const [entries, setEntries] = useState<ForensicTraceEntry[]>([]);
  const [anomalies, setAnomalies] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [hasQueried, setHasQueried] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const currentFilters = useCallback((): ForensicTraceFilters => {
    const filters: ForensicTraceFilters = {};
    if (actor.trim()) filters.actor = actor.trim();
    if (subject.trim()) filters.subject = subject.trim();
    if (category) filters.category = category;
    if (since) filters.since = new Date(since).toISOString();
    if (until) filters.until = new Date(until).toISOString();
    return filters;
  }, [actor, subject, category, since, until]);

  async function handleQuery(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !user) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await getForensicTrace(accessToken, user.username, currentFilters());
      setEntries(result.entries);
      setAnomalies(result.anomalies);
      setHasQueried(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("forensicTrace.loadError"));
    } finally {
      setIsLoading(false);
    }
  }

  async function handleExport(format: "csv" | "pdf") {
    if (!accessToken || !user) return;
    setExportError(null);
    try {
      const blob = await exportForensicTrace(accessToken, user.username, format, currentFilters());
      triggerBrowserDownload(blob, `forensic-trace.${format}`);
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : t("forensicTrace.exportError"));
    }
  }

  if (!accessToken) return null;

  return (
    <div>
      <p className="hint">{t("forensicTrace.hint")}</p>

      <section className="card">
        <form className="explorer-toolbar" onSubmit={handleQuery}>
          <input
            placeholder={t("forensicTrace.filterActor")}
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
          <input
            placeholder={t("forensicTrace.filterSubject")}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value as ForensicTraceCategory | "")}
            aria-label={t("forensicTrace.filterCategory")}
          >
            <option value="">{t("forensicTrace.categoryAll")}</option>
            <option value="view">{t("forensicTrace.categoryView")}</option>
            <option value="download">{t("forensicTrace.categoryDownload")}</option>
            <option value="change">{t("forensicTrace.categoryChange")}</option>
            <option value="delete">{t("forensicTrace.categoryDelete")}</option>
          </select>
          <label>
            {t("forensicTrace.since")}
            <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
          <label>
            {t("forensicTrace.until")}
            <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
          <button type="submit" disabled={isLoading}>
            {t("forensicTrace.query")}
          </button>
          <span className="actions">
            <button type="button" onClick={() => handleExport("csv")}>
              {t("forensicTrace.exportCsv")}
            </button>
            <button type="button" onClick={() => handleExport("pdf")}>
              {t("forensicTrace.exportPdf")}
            </button>
          </span>
        </form>

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        {exportError && (
          <p className="error-text" role="alert">
            {exportError}
          </p>
        )}

        {anomalies.length > 0 && (
          <div className="error-text" role="alert">
            <strong>{t("forensicTrace.anomaliesHeading")}</strong>
            <ul>
              {anomalies.map((anomaly, index) => (
                <li key={index}>{anomaly}</li>
              ))}
            </ul>
          </div>
        )}

        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : !hasQueried ? (
          <p className="empty-state">{t("forensicTrace.notYetQueried")}</p>
        ) : entries.length === 0 ? (
          <p className="empty-state">{t("forensicTrace.empty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("forensicTrace.occurredAt")}</th>
                <th>{t("forensicTrace.eventType")}</th>
                <th>{t("forensicTrace.category")}</th>
                <th>{t("forensicTrace.actor")}</th>
                <th>{t("forensicTrace.subject")}</th>
                <th>{t("forensicTrace.serviceName")}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td>{new Date(entry.occurred_at).toLocaleString()}</td>
                  <td>{entry.event_type}</td>
                  <td>{t(`forensicTrace.category${capitalize(entry.category)}`)}</td>
                  <td>{entry.actor ?? "—"}</td>
                  <td>{entry.subject ?? "—"}</td>
                  <td>{entry.service_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
