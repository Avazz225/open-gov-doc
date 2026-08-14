"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createReportSchedule,
  deleteReportSchedule,
  exportReport,
  getDocumentVolumeReport,
  getOpenWorkflowTasksReport,
  getStorageUsageReport,
  getUserActivityReport,
  listReportSchedules,
  type DocumentVolumeEntry,
  type OpenWorkflowTaskEntry,
  type ReportFormat,
  type ReportFrequency,
  type ReportSchedule,
  type ReportType,
  type StorageUsageReportEntry,
  type UserActivityEntry,
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

// Standard reports (5.4a, since P7-S2b) - four fixed report types against
// the new reporting-service, each with a table + CSV/PDF export, plus
// management of schedulable, email-delivered runs. Deliberately in the
// admin UI (not the user UI) - system analytics/user activity is an
// administrative concern, same role split as e.g. MaintenanceBanner.
export function ReportsView() {
  const { accessToken } = useAuth();

  return (
    <div>
      <DocumentVolumeSection token={accessToken ?? ""} />
      <OpenWorkflowTasksSection token={accessToken ?? ""} />
      <StorageUsageSection token={accessToken ?? ""} />
      <UserActivitySection token={accessToken ?? ""} />
      <ReportScheduleSection token={accessToken ?? ""} />
    </div>
  );
}

function ExportButtons({
  token,
  reportType,
  extraParams,
}: {
  token: string;
  reportType: ReportType;
  extraParams?: Record<string, string | undefined>;
}) {
  const { t } = useI18n();
  const [error, setError] = useState<string | null>(null);

  async function handleExport(format: ReportFormat) {
    setError(null);
    try {
      const blob = await exportReport(token, reportType, format, extraParams);
      triggerBrowserDownload(blob, `${reportType}.${format}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.exportError"));
    }
  }

  return (
    <span className="actions">
      <button type="button" onClick={() => handleExport("csv")}>
        {t("reports.exportCsv")}
      </button>
      <button type="button" onClick={() => handleExport("pdf")}>
        {t("reports.exportPdf")}
      </button>
      {error && (
        <span className="error-text" role="alert">
          {error}
        </span>
      )}
    </span>
  );
}

function DocumentVolumeSection({ token }: { token: string }) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<DocumentVolumeEntry[]>([]);
  const [groupBy, setGroupBy] = useState<"day" | "week" | "month">("day");
  const [folderId, setFolderId] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setEntries(
        await getDocumentVolumeReport(token, { groupBy, folderId: folderId || undefined })
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, groupBy, folderId, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card">
      <h2 className="pane-heading">{t("reports.documentVolumeHeading")}</h2>
      <div className="explorer-toolbar">
        <label>
          {t("reports.groupBy")}{" "}
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as typeof groupBy)}>
            <option value="day">{t("reports.groupByDay")}</option>
            <option value="week">{t("reports.groupByWeek")}</option>
            <option value="month">{t("reports.groupByMonth")}</option>
          </select>
        </label>
        <input
          placeholder={t("reports.filterFolderId")}
          value={folderId}
          onChange={(e) => setFolderId(e.target.value)}
        />
        <button type="button" onClick={load}>
          {t("reports.reload")}
        </button>
        <ExportButtons token={token} reportType="document_volume" extraParams={{ group_by: groupBy, folder_id: folderId || undefined }} />
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="empty-state">{t("reports.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("reports.period")}</th>
              <th>{t("reports.folderId")}</th>
              <th>{t("reports.count")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry, index) => (
              <tr key={index}>
                <td>{entry.period}</td>
                <td>{entry.folder_id ?? "—"}</td>
                <td>{entry.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function OpenWorkflowTasksSection({ token }: { token: string }) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<OpenWorkflowTaskEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setEntries(await getOpenWorkflowTasksReport(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card">
      <h2 className="pane-heading">{t("reports.openWorkflowTasksHeading")}</h2>
      <div className="explorer-toolbar">
        <button type="button" onClick={load}>
          {t("reports.reload")}
        </button>
        <ExportButtons token={token} reportType="open_workflow_tasks" />
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="empty-state">{t("reports.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("reports.instanceId")}</th>
              <th>{t("reports.businessKey")}</th>
              <th>{t("reports.taskName")}</th>
              <th>{t("reports.lane")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={`${entry.instance_id}-${entry.task_id}`}>
                <td>{entry.instance_id}</td>
                <td>{entry.business_key ?? "—"}</td>
                <td>{entry.task_name}</td>
                <td>{entry.lane ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function StorageUsageSection({ token }: { token: string }) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<StorageUsageReportEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setEntries(await getStorageUsageReport(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card">
      <h2 className="pane-heading">{t("reports.storageUsageHeading")}</h2>
      <div className="explorer-toolbar">
        <button type="button" onClick={load}>
          {t("reports.reload")}
        </button>
        <ExportButtons token={token} reportType="storage_usage" />
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="empty-state">{t("reports.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("reports.backend")}</th>
              <th>{t("reports.objectCount")}</th>
              <th>{t("reports.totalSizeBytes")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.backend}>
                <td>{entry.backend}</td>
                <td>{entry.object_count}</td>
                <td>{entry.total_size_bytes.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function UserActivitySection({ token }: { token: string }) {
  const { t } = useI18n();
  const [entries, setEntries] = useState<UserActivityEntry[]>([]);
  const [actor, setActor] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setEntries(await getUserActivityReport(token, { actor: actor || undefined }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, actor, t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="card">
      <h2 className="pane-heading">{t("reports.userActivityHeading")}</h2>
      <div className="explorer-toolbar">
        <input
          placeholder={t("reports.filterActor")}
          value={actor}
          onChange={(e) => setActor(e.target.value)}
        />
        <button type="button" onClick={load}>
          {t("reports.reload")}
        </button>
        <ExportButtons token={token} reportType="user_activity" extraParams={{ actor: actor || undefined }} />
      </div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : entries.length === 0 ? (
        <p className="empty-state">{t("reports.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("reports.actor")}</th>
              <th>{t("reports.eventType")}</th>
              <th>{t("reports.count")}</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => (
              <tr key={`${entry.actor}-${entry.event_type}`}>
                <td>{entry.actor}</td>
                <td>{entry.event_type}</td>
                <td>{entry.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ReportScheduleSection({ token }: { token: string }) {
  const { t } = useI18n();
  const [schedules, setSchedules] = useState<ReportSchedule[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reportType, setReportType] = useState<ReportType>("document_volume");
  const [format, setFormat] = useState<ReportFormat>("csv");
  const [frequency, setFrequency] = useState<ReportFrequency>("weekly");
  const [recipientEmail, setRecipientEmail] = useState("");

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setSchedules(await listReportSchedules(token));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!recipientEmail.trim()) return;
    try {
      await createReportSchedule(token, { reportType, format, frequency, recipientEmail });
      setRecipientEmail("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.scheduleError"));
    }
  }

  async function handleDelete(scheduleId: string) {
    try {
      await deleteReportSchedule(token, scheduleId);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("reports.scheduleError"));
    }
  }

  return (
    <section className="card">
      <h2 className="pane-heading">{t("reports.schedulesHeading")}</h2>
      <p className="hint">{t("reports.schedulesHint")}</p>
      <form className="inline-form" aria-label={t("reports.newScheduleFormLabel")} onSubmit={handleCreate}>
        <select value={reportType} onChange={(e) => setReportType(e.target.value as ReportType)}>
          <option value="document_volume">{t("reports.documentVolumeHeading")}</option>
          <option value="open_workflow_tasks">{t("reports.openWorkflowTasksHeading")}</option>
          <option value="storage_usage">{t("reports.storageUsageHeading")}</option>
          <option value="user_activity">{t("reports.userActivityHeading")}</option>
        </select>
        <select value={format} onChange={(e) => setFormat(e.target.value as ReportFormat)}>
          <option value="csv">CSV</option>
          <option value="pdf">PDF</option>
        </select>
        <select value={frequency} onChange={(e) => setFrequency(e.target.value as ReportFrequency)}>
          <option value="daily">{t("reports.frequencyDaily")}</option>
          <option value="weekly">{t("reports.frequencyWeekly")}</option>
          <option value="monthly">{t("reports.frequencyMonthly")}</option>
        </select>
        <input
          type="email"
          placeholder={t("reports.recipientEmail")}
          value={recipientEmail}
          onChange={(e) => setRecipientEmail(e.target.value)}
          required
        />
        <button type="submit">{t("reports.createSchedule")}</button>
      </form>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : schedules.length === 0 ? (
        <p className="empty-state">{t("reports.schedulesEmpty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("reports.reportType")}</th>
              <th>{t("reports.format")}</th>
              <th>{t("reports.frequency")}</th>
              <th>{t("reports.recipientEmail")}</th>
              <th>{t("reports.nextRunAt")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td>{schedule.report_type}</td>
                <td>{schedule.format}</td>
                <td>{schedule.frequency}</td>
                <td>{schedule.recipient_email}</td>
                <td>{new Date(schedule.next_run_at).toLocaleString()}</td>
                <td>
                  <button type="button" onClick={() => handleDelete(schedule.id)}>
                    {t("reports.deleteSchedule")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
