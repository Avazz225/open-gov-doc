"use client";

import { useCallback, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, listQueryEvents, type QueryEvent, type QueryEventFilters } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Query- & Trace-Konsole (6.1, seit P8-S1) - strukturierte, RBAC-gefilterte
// Lesezugriffe auf audit-service's Ereignisliste ueber query-service. Anders
// als der Forensik-Trace (5.4b) filtert query-service Ergebniszeilen aktiv
// nach Ordnerberechtigung der ausfuehrenden Person (siehe
// docs/services/query-service.md). Kein Freitext-SQL-Feld in dieser
// Session - der `POST /query`-Pfad bleibt ohne installiertes Parser-Plugin
// (ADR 0031) ungenutzt, siehe Architekturentscheidung in PROGRESS.md.
export function QueryConsoleView() {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  const [actor, setActor] = useState("");
  const [subject, setSubject] = useState("");
  const [eventType, setEventType] = useState("");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");

  const [events, setEvents] = useState<QueryEvent[]>([]);
  const [totalBeforeFilter, setTotalBeforeFilter] = useState(0);
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [hasQueried, setHasQueried] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const currentFilters = useCallback((): QueryEventFilters => {
    const filters: QueryEventFilters = {};
    if (actor.trim()) filters.actor = actor.trim();
    if (subject.trim()) filters.subject = subject.trim();
    if (eventType.trim()) filters.eventType = eventType.trim();
    if (since) filters.since = new Date(since).toISOString();
    if (until) filters.until = new Date(until).toISOString();
    return filters;
  }, [actor, subject, eventType, since, until]);

  async function handleQuery(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const result = await listQueryEvents(accessToken, currentFilters());
      setEvents(result.events);
      setTotalBeforeFilter(result.total_before_filter);
      setIsSuperuser(result.superuser);
      setHasQueried(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    } finally {
      setIsLoading(false);
    }
  }

  if (!accessToken) return null;

  return (
    <div>
      <p className="hint">{t("queryConsole.hint")}</p>

      <section className="card">
        <form className="explorer-toolbar" onSubmit={handleQuery}>
          <input
            placeholder={t("queryConsole.filterActor")}
            value={actor}
            onChange={(e) => setActor(e.target.value)}
          />
          <input
            placeholder={t("queryConsole.filterSubject")}
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
          <input
            placeholder={t("queryConsole.filterEventType")}
            value={eventType}
            onChange={(e) => setEventType(e.target.value)}
          />
          <label>
            {t("queryConsole.since")}
            <input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} />
          </label>
          <label>
            {t("queryConsole.until")}
            <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
          <button type="submit" disabled={isLoading}>
            {t("queryConsole.query")}
          </button>
        </form>

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        {hasQueried && !error && (
          <p className="hint">
            {isSuperuser
              ? t("queryConsole.superuserHint")
              : t("queryConsole.filteredHint", {
                  visible: events.length,
                  total: totalBeforeFilter,
                })}
          </p>
        )}

        {isLoading ? (
          <p>{t("common.loading")}</p>
        ) : !hasQueried ? (
          <p className="empty-state">{t("queryConsole.notYetQueried")}</p>
        ) : events.length === 0 ? (
          <p className="empty-state">{t("queryConsole.empty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("queryConsole.occurredAt")}</th>
                <th>{t("queryConsole.eventType")}</th>
                <th>{t("queryConsole.actor")}</th>
                <th>{t("queryConsole.subject")}</th>
                <th>{t("queryConsole.serviceName")}</th>
              </tr>
            </thead>
            <tbody>
              {events.map((entry) => (
                <tr key={entry.id}>
                  <td>{new Date(entry.occurred_at).toLocaleString()}</td>
                  <td>{entry.event_type}</td>
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
