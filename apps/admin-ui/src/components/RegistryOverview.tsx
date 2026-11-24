"use client";

import { Fragment, useCallback, useEffect, useState, type FormEvent } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type ReplicaType,
  type ServiceInstance,
  forgetServiceInstance,
  listReplicas,
  listServiceInstances,
  resetExpectedReplicas,
  setExpectedReplicas,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { BoundedList } from "@/components/BoundedList";

// Registry overview grouped by service type (P87-S3, ADR 0241): per type the
// reachable count against the expected range and where that range comes
// from (admin override, Helm default, none), the committed alarm state and
// since when. A type expands to its instances; an unreachable one can be
// forgotten by hand. Editing and forgetting need `admin.monitoring` - the
// server checks it too, the UI only hides what would be refused.
// Replaced instances are forgotten by registry-service itself (they no
// longer pile up here); each forget is an audit event,
// `registry.instance.forgotten`, with its reason.
const STATE_BADGE: Record<ReplicaType["state"], string> = {
  ok: "ok",
  under: "down",
  vanished: "down",
  over: "down",
  unmonitored: "neutral",
};

function rangeLabel(row: ReplicaType): string {
  if (row.expected_min === null) return "—";
  if (row.expected_max === null) return `≥ ${row.expected_min}`;
  if (row.expected_max === row.expected_min) return String(row.expected_min);
  return `${row.expected_min}–${row.expected_max}`;
}

export function RegistryOverview() {
  const { accessToken, permissions } = useAuth();
  const { t, locale } = useI18n();
  const canManage = permissions.includes("admin.monitoring");
  const [types, setTypes] = useState<ReplicaType[]>([]);
  const [instances, setInstances] = useState<ServiceInstance[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editing, setEditing] = useState<string | null>(null);
  const [minDraft, setMinDraft] = useState("1");
  const [maxDraft, setMaxDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setError(null);
    try {
      const [loadedTypes, loadedInstances] = await Promise.all([
        listReplicas(accessToken),
        listServiceInstances(accessToken),
      ]);
      setTypes(loadedTypes);
      setInstances(loadedInstances);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  function toggle(serviceType: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(serviceType)) next.delete(serviceType);
      else next.add(serviceType);
      return next;
    });
  }

  function startEdit(row: ReplicaType) {
    setEditing(row.service_type);
    setMinDraft(String(row.expected_min ?? Math.max(1, row.reachable)));
    setMaxDraft(row.expected_max === null ? "" : String(row.expected_max));
    setError(null);
  }

  async function run(action: () => Promise<unknown>) {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      setEditing(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  function saveExpectation(serviceType: string, event: FormEvent) {
    event.preventDefault();
    const min = Number(minDraft);
    const max = maxDraft.trim() === "" ? null : Number(maxDraft);
    if (!Number.isInteger(min) || min < 1 || (max !== null && (!Number.isInteger(max) || max < min))) {
      setError(t("registry.invalidRange"));
      return;
    }
    run(() => setExpectedReplicas(accessToken ?? "", serviceType, { min_replicas: min, max_replicas: max }));
  }

  const formatDate = (value: string | null) =>
    value ? new Date(value).toLocaleString(locale) : "—";

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const fieldInput =
    "w-20 rounded-md border border-border bg-bg px-2 py-1 text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg";

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button type="button" className={primaryBtn} onClick={reload}>
          {t("common.refresh")}
        </button>
        <p className="m-0 text-sm opacity-70">{t("registry.hint")}</p>
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : types.length === 0 ? (
        <p className="italic opacity-70">{t("registry.empty")}</p>
      ) : (
        <BoundedList label={t("nav.registry")} total={types.length}>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("registry.serviceType")}</th>
                <th>{t("registry.reachable")}</th>
                <th>{t("registry.expected")}</th>
                <th>{t("registry.state")}</th>
                {canManage && <th />}
              </tr>
            </thead>
            <tbody>
              {types.map((row) => {
                const isOpen = expanded.has(row.service_type);
                const own = instances.filter((i) => i.service_type === row.service_type);
                return (
                  <Fragment key={row.service_type}>
                    <tr>
                      <td>
                        <button
                          type="button"
                          className="flex items-center gap-1 whitespace-nowrap border-0 bg-transparent p-0 text-left text-fg"
                          aria-expanded={isOpen}
                          onClick={() => toggle(row.service_type)}
                        >
                          {isOpen ? (
                            <ChevronDown size={16} aria-hidden="true" className="flex-none" />
                          ) : (
                            <ChevronRight size={16} aria-hidden="true" className="flex-none" />
                          )}
                          {row.service_type}
                        </button>
                      </td>
                      <td className="whitespace-nowrap">
                        {row.reachable}
                        {row.draining > 0 && (
                          <span className="opacity-70"> · {t("registry.draining", { count: row.draining })}</span>
                        )}
                        {row.unreachable > 0 && (
                          <span className="opacity-70"> · {t("registry.unreachable", { count: row.unreachable })}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap">
                        {rangeLabel(row)}
                        {row.source !== "none" && (
                          <span className="opacity-70"> ({t(`registry.source.${row.source}`)})</span>
                        )}
                      </td>
                      <td>
                        <span className={`badge ${STATE_BADGE[row.state]}`}>{t(`registry.states.${row.state}`)}</span>
                        {row.state !== "unmonitored" && row.since && (
                          <span className="ml-2 text-sm opacity-70">
                            {t("registry.since", { date: formatDate(row.since) })}
                          </span>
                        )}
                        {row.pending_state && (
                          <span className="ml-2 text-sm opacity-70">
                            {t("registry.pending", { state: t(`registry.states.${row.pending_state}`) })}
                          </span>
                        )}
                      </td>
                      {canManage && (
                        <td>
                          <button
                            type="button"
                            className={secondaryBtn}
                            onClick={() => startEdit(row)}
                            disabled={busy}
                          >
                            {t("registry.editExpectation")}
                          </button>
                        </td>
                      )}
                    </tr>
                    {editing === row.service_type && (
                      <tr className="bg-hover-bg">
                        <td colSpan={canManage ? 5 : 4}>
                          <form
                            aria-label={t("registry.expectationFormLabel", { type: row.service_type })}
                            className="flex flex-wrap items-end gap-3"
                            onSubmit={(event) => saveExpectation(row.service_type, event)}
                          >
                            <label className="flex flex-col gap-1 text-sm">
                              {t("registry.min")}
                              <input
                                type="number"
                                min={1}
                                value={minDraft}
                                onChange={(e) => setMinDraft(e.target.value)}
                                className={fieldInput}
                                required
                              />
                            </label>
                            <label className="flex flex-col gap-1 text-sm">
                              {t("registry.max")}
                              <input
                                type="number"
                                min={1}
                                value={maxDraft}
                                onChange={(e) => setMaxDraft(e.target.value)}
                                placeholder={t("registry.maxPlaceholder")}
                                className={fieldInput}
                              />
                            </label>
                            <span className="flex flex-wrap gap-3">
                              <button type="submit" className={primaryBtn} disabled={busy}>
                                {t("common.save")}
                              </button>
                              {row.source === "admin" && (
                                <button
                                  type="button"
                                  className={secondaryBtn}
                                  disabled={busy}
                                  onClick={() => run(() => resetExpectedReplicas(accessToken ?? "", row.service_type))}
                                >
                                  {t("registry.resetExpectation")}
                                </button>
                              )}
                              <button type="button" className={secondaryBtn} onClick={() => setEditing(null)}>
                                {t("common.cancel")}
                              </button>
                            </span>
                          </form>
                        </td>
                      </tr>
                    )}
                    {isOpen && (
                      <tr>
                        <td colSpan={canManage ? 5 : 4} className="pl-6">
                          {own.length === 0 ? (
                            <p className="m-0 text-sm italic opacity-70">{t("registry.noInstances")}</p>
                          ) : (
                            <table className="data-table">
                              <thead>
                                <tr>
                                  <th>{t("registry.instance")}</th>
                                  <th>{t("registry.version")}</th>
                                  <th>{t("registry.address")}</th>
                                  <th>{t("registry.lastSeen")}</th>
                                  <th>{t("registry.status")}</th>
                                  {canManage && <th />}
                                </tr>
                              </thead>
                              <tbody>
                                {own.map((i) => (
                                  <tr key={i.instance_id}>
                                    <td>
                                      <code className="font-mono text-xs">{i.instance_id}</code>
                                    </td>
                                    <td>{i.version}</td>
                                    <td>{i.address}</td>
                                    <td className="whitespace-nowrap">{formatDate(i.last_heartbeat_at)}</td>
                                    <td>
                                      <span className={`badge ${i.healthy ? "ok" : "down"}`}>
                                        {i.healthy
                                          ? i.status === "draining"
                                            ? t("registry.drainingBadge")
                                            : t("registry.healthy")
                                          : t("registry.down")}
                                      </span>
                                    </td>
                                    {canManage && (
                                      <td>
                                        {!i.healthy && (
                                          <button
                                            type="button"
                                            className={secondaryBtn}
                                            disabled={busy}
                                            onClick={() => {
                                              if (window.confirm(t("registry.forgetConfirm", { id: i.instance_id })))
                                                run(() => forgetServiceInstance(accessToken ?? "", i.instance_id));
                                            }}
                                          >
                                            {t("registry.forget")}
                                          </button>
                                        )}
                                      </td>
                                    )}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </BoundedList>
      )}
    </>
  );
}
