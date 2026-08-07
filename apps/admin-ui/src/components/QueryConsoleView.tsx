"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  activateManipulationMode,
  approveApprovalRequest,
  deactivateManipulationMode,
  dryRunManipulation,
  executeManipulation,
  getManipulationModeStatus,
  listPendingManipulationApprovals,
  listQueryEvents,
  type ApprovalRequest,
  type DryRunResult,
  type ManipulateExecuteResult,
  type ManipulationActionType,
  type ManipulationModeStatus,
  type QueryEvent,
  type QueryEventFilters,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Query- & Trace-Konsole (6.1, seit P8-S1/P8-S2/P8-S2b) - strukturierte,
// RBAC-gefilterte Lesezugriffe auf audit-service's Ereignisliste ueber
// query-service (Lesezugriff, siehe QueryEventsSection) sowie seit P8-S2 die
// Manipulationsseite (Schutzschalter, Dry-Run, Vier-Augen, siehe
// ManipulationSection) - siehe docs/services/query-service.md. Kein
// Freitext-SQL-Feld in dieser Session - der `POST /query`-Pfad bleibt ohne
// installiertes Parser-Plugin (ADR 0031) ungenutzt.
export function QueryConsoleView() {
  return (
    <div>
      <QueryEventsSection />
      <ManipulationSection />
    </div>
  );
}

function QueryEventsSection() {
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

const DEFAULT_ACTION_TYPE: ManipulationActionType = "document.attribute_reset";

// Manipulationsmodus (6.1, seit P8-S2/P8-S2b) - Schutzschalter, Dry-Run,
// Vier-Augen. Nur mit der Capability `admin.query_console.manipulate`
// sichtbar - Tiefen-Verteidigung, die eigentliche Durchsetzung passiert
// serverseitig in query-service (403 ohne Rolle/aktiven Schutzschalter).
function ManipulationSection() {
  const { accessToken, permissions, user } = useAuth();
  const { t } = useI18n();

  const [modeStatus, setModeStatus] = useState<ManipulationModeStatus | null>(null);
  const [modeError, setModeError] = useState<string | null>(null);
  const [durationMinutes, setDurationMinutes] = useState("15");

  const [actionType, setActionType] = useState<ManipulationActionType>(DEFAULT_ACTION_TYPE);
  const [documentId, setDocumentId] = useState("");
  const [attributeKey, setAttributeKey] = useState("");
  const [roleAssignmentId, setRoleAssignmentId] = useState("");
  const [objectTypeId, setObjectTypeId] = useState("");
  const [objectTypeField, setObjectTypeField] = useState<"naming_constraints" | "conditions">(
    "naming_constraints"
  );
  const [objectTypeValue, setObjectTypeValue] = useState("");

  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [dryRunError, setDryRunError] = useState<string | null>(null);
  const [executeResult, setExecuteResult] = useState<ManipulateExecuteResult | null>(null);
  const [executeError, setExecuteError] = useState<string | null>(null);

  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>([]);
  const [approvalsError, setApprovalsError] = useState<string | null>(null);

  const canManipulate = permissions.includes("admin.query_console.manipulate");

  const reloadModeStatus = useCallback(async () => {
    if (!accessToken) return;
    try {
      setModeStatus(await getManipulationModeStatus(accessToken));
      setModeError(null);
    } catch (err) {
      setModeError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }, [accessToken, t]);

  const reloadPendingApprovals = useCallback(async () => {
    if (!accessToken) return;
    try {
      setPendingApprovals(await listPendingManipulationApprovals(accessToken));
      setApprovalsError(null);
    } catch (err) {
      setApprovalsError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    if (!canManipulate) return;
    reloadModeStatus();
    reloadPendingApprovals();
  }, [canManipulate, reloadModeStatus, reloadPendingApprovals]);

  function resetDryRun() {
    setDryRun(null);
    setExecuteResult(null);
    setExecuteError(null);
  }

  async function handleActivate() {
    if (!accessToken) return;
    setModeError(null);
    try {
      setModeStatus(await activateManipulationMode(accessToken, Number(durationMinutes)));
    } catch (err) {
      setModeError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }

  async function handleDeactivate() {
    if (!accessToken) return;
    setModeError(null);
    try {
      setModeStatus(await deactivateManipulationMode(accessToken));
    } catch (err) {
      setModeError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }

  function buildParams(): Record<string, unknown> | null {
    setDryRunError(null);
    if (actionType === "document.attribute_reset") {
      return { document_id: documentId, attribute_key: attributeKey };
    }
    if (actionType === "permission.role_assignment.delete") {
      return { role_assignment_id: Number(roleAssignmentId) };
    }
    try {
      const value = objectTypeValue.trim() ? JSON.parse(objectTypeValue) : null;
      return { object_type_id: Number(objectTypeId), field: objectTypeField, value };
    } catch {
      setDryRunError(t("queryConsole.manipulationValueParseError"));
      return null;
    }
  }

  async function handleDryRun(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    const params = buildParams();
    if (params === null) return;
    setDryRunError(null);
    setExecuteResult(null);
    setExecuteError(null);
    try {
      setDryRun(await dryRunManipulation(accessToken, actionType, params));
    } catch (err) {
      setDryRunError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }

  async function handleExecute() {
    if (!accessToken || !dryRun) return;
    setExecuteError(null);
    try {
      const result = await executeManipulation(accessToken, dryRun.dry_run_token);
      setExecuteResult(result);
      if (result.status === "pending_approval") {
        await reloadPendingApprovals();
      }
    } catch (err) {
      setExecuteError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }

  async function handleApprove(requestId: string) {
    if (!accessToken || !user) return;
    setApprovalsError(null);
    try {
      await approveApprovalRequest(accessToken, requestId, user.username);
      await reloadPendingApprovals();
    } catch (err) {
      setApprovalsError(err instanceof ApiError ? err.message : t("queryConsole.loadError"));
    }
  }

  if (!accessToken || !canManipulate) return null;

  return (
    <div>
      <h2>{t("queryConsole.manipulationTitle")}</h2>
      <p className="hint">{t("queryConsole.manipulationHint")}</p>

      <section className="card">
        <h3>{t("queryConsole.schutzschalterTitle")}</h3>
        {modeError && (
          <p className="error-text" role="alert">
            {modeError}
          </p>
        )}
        <p>
          <strong>
            {modeStatus?.active
              ? t("queryConsole.schutzschalterActive")
              : t("queryConsole.schutzschalterInactive")}
          </strong>
          {modeStatus?.active && modeStatus.expires_at && (
            <>
              {" "}
              — {t("queryConsole.schutzschalterExpiresAt")}: {modeStatus.expires_at}
            </>
          )}
        </p>
        {!modeStatus?.active ? (
          <div className="explorer-toolbar">
            <label>
              {t("queryConsole.schutzschalterDurationLabel")}
              <input
                type="number"
                min={1}
                value={durationMinutes}
                onChange={(e) => setDurationMinutes(e.target.value)}
              />
            </label>
            <button type="button" onClick={handleActivate}>
              {t("queryConsole.schutzschalterActivate")}
            </button>
          </div>
        ) : (
          <button type="button" onClick={handleDeactivate}>
            {t("queryConsole.schutzschalterDeactivate")}
          </button>
        )}
      </section>

      <section className="card">
        <h3>{t("queryConsole.manipulateActionTitle")}</h3>
        <form className="explorer-toolbar" onSubmit={handleDryRun}>
          <label>
            {t("queryConsole.actionTypeLabel")}
            <select
              value={actionType}
              onChange={(e) => {
                setActionType(e.target.value as ManipulationActionType);
                resetDryRun();
              }}
            >
              <option value="document.attribute_reset">
                {t("queryConsole.actionDocumentAttributeReset")}
              </option>
              <option value="permission.role_assignment.delete">
                {t("queryConsole.actionRoleAssignmentDelete")}
              </option>
              <option value="object_type.update">{t("queryConsole.actionObjectTypeUpdate")}</option>
            </select>
          </label>

          {actionType === "document.attribute_reset" && (
            <>
              <input
                placeholder={t("queryConsole.documentIdLabel")}
                value={documentId}
                onChange={(e) => {
                  setDocumentId(e.target.value);
                  resetDryRun();
                }}
              />
              <input
                placeholder={t("queryConsole.attributeKeyLabel")}
                value={attributeKey}
                onChange={(e) => {
                  setAttributeKey(e.target.value);
                  resetDryRun();
                }}
              />
            </>
          )}

          {actionType === "permission.role_assignment.delete" && (
            <input
              type="number"
              placeholder={t("queryConsole.roleAssignmentIdLabel")}
              value={roleAssignmentId}
              onChange={(e) => {
                setRoleAssignmentId(e.target.value);
                resetDryRun();
              }}
            />
          )}

          {actionType === "object_type.update" && (
            <>
              <input
                type="number"
                placeholder={t("queryConsole.objectTypeIdLabel")}
                value={objectTypeId}
                onChange={(e) => {
                  setObjectTypeId(e.target.value);
                  resetDryRun();
                }}
              />
              <select
                value={objectTypeField}
                onChange={(e) => {
                  setObjectTypeField(e.target.value as "naming_constraints" | "conditions");
                  resetDryRun();
                }}
              >
                <option value="naming_constraints">
                  {t("queryConsole.fieldNamingConstraints")}
                </option>
                <option value="conditions">{t("queryConsole.fieldConditions")}</option>
              </select>
              <textarea
                placeholder={t("queryConsole.valueJsonLabel")}
                value={objectTypeValue}
                onChange={(e) => {
                  setObjectTypeValue(e.target.value);
                  resetDryRun();
                }}
              />
            </>
          )}

          <button type="submit">{t("queryConsole.dryRunButton")}</button>
        </form>

        {dryRunError && (
          <p className="error-text" role="alert">
            {dryRunError}
          </p>
        )}

        {dryRun && (
          <div>
            <p>{dryRun.preview}</p>
            {dryRun.is_critical && (
              <p className="error-text">{t("queryConsole.criticalBadge")}</p>
            )}
            <button type="button" onClick={handleExecute}>
              {t("queryConsole.executeButton")}
            </button>
          </div>
        )}

        {executeError && (
          <p className="error-text" role="alert">
            {executeError}
          </p>
        )}
        {executeResult?.status === "executed" && (
          <p>{t("queryConsole.executedResult")}</p>
        )}
        {executeResult?.status === "pending_approval" && (
          <p>{t("queryConsole.pendingApprovalResult")}</p>
        )}
      </section>

      <section className="card">
        <h3>{t("queryConsole.pendingApprovalsTitle")}</h3>
        {approvalsError && (
          <p className="error-text" role="alert">
            {approvalsError}
          </p>
        )}
        {pendingApprovals.length === 0 ? (
          <p className="empty-state">{t("queryConsole.pendingApprovalsEmpty")}</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("queryConsole.actionTypeLabel")}</th>
                <th>{t("queryConsole.initiatedBy")}</th>
                <th>{t("queryConsole.createdAt")}</th>
                <th>{t("queryConsole.parameters")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {pendingApprovals.map((approval) => (
                <tr key={approval.id}>
                  <td>{approval.action_type}</td>
                  <td>{approval.initiated_by}</td>
                  <td>{new Date(approval.created_at).toLocaleString()}</td>
                  <td>{JSON.stringify(approval.payload)}</td>
                  <td>
                    <button type="button" onClick={() => handleApprove(approval.id)}>
                      {t("queryConsole.approveButton")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
