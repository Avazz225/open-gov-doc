"use client";

import { Fragment, useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  addFleetGroupMember,
  cancelFleetRollout,
  createFleetGroup,
  createFleetOperator,
  createFleetPlan,
  createFleetRollout,
  deleteFleetGroup,
  deleteFleetPlan,
  deleteFleetRollout,
  fleetRunAction,
  listFleetGroups,
  listFleetOperators,
  listFleetPlans,
  listFleetRollouts,
  removeFleetGroupMember,
  revokeFleetOperator,
  startFleetRollout,
  type FleetGateOutcome,
  type FleetGroup,
  type FleetOperator,
  type FleetPlan,
  type FleetPlanStep,
  type FleetRollout,
  type FleetRun,
  type FleetRunAction,
  type ManagedInstallation,
} from "@/lib/api";

// Fleet update orchestration in admin-ui (P88-S1): groups (waves), update
// plans, rollouts with each installation's run, and operator management.
// The backend has existed since P13-S2b and was curl-only until now. The
// acting operator is always the one whose token was entered: the server
// takes proposer, approver and starter from it (P84-S4, ADR 0234), so the
// UI only greys out what the server would refuse (approving one's own
// proposal) and explains why.

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
const card = "rounded-lg border border-border p-4 mb-6";

const RUN_BADGE: Record<FleetRun["status"], string> = {
  pending: "neutral",
  wait_external: "neutral",
  manual_required: "neutral",
  retry_later: "down",
  recoverable_failed: "down",
  fatal_contract: "down",
  completed: "ok",
};

const ROLLOUT_BADGE: Record<FleetRollout["status"], string> = {
  draft: "neutral",
  running: "neutral",
  completed: "ok",
  cancelled: "down",
};

const GATE_OUTCOMES: FleetGateOutcome[] = ["success", "retry_later", "recoverable_failed", "fatal_contract"];

interface Props {
  operatorKey: string;
  operator: FleetOperator;
  installations: ManagedInstallation[];
}

// Busy flag plus a per-section error, so one failing action doesn't hide
// another section's state.
function useAction(fallback: string) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(
    async (action: () => Promise<unknown>): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        await action();
        return true;
      } catch (err) {
        setError(err instanceof ApiError ? err.message : fallback);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [fallback]
  );
  return { busy, error, setError, run };
}

function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="text-danger" role="alert">
      {error}
    </p>
  );
}

export function FleetOrchestration({ operatorKey, operator, installations }: Props) {
  const { t } = useI18n();
  const [groups, setGroups] = useState<FleetGroup[]>([]);
  const [plans, setPlans] = useState<FleetPlan[]>([]);
  const [rollouts, setRollouts] = useState<FleetRollout[]>([]);
  const [operators, setOperators] = useState<FleetOperator[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const [g, p, r, o] = await Promise.all([
        listFleetGroups(operatorKey),
        listFleetPlans(operatorKey),
        listFleetRollouts(operatorKey),
        listFleetOperators(operatorKey),
      ]);
      setGroups(g);
      setPlans(p);
      setRollouts(r);
      setOperators(o);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t("fleetManagement.unreachable"));
    }
  }, [operatorKey, t]);

  // Reloads when the installation list changes, too: removing an
  // installation drops its group memberships on the server.
  useEffect(() => {
    reload();
  }, [reload, installations]);

  const installationName = useMemo(() => {
    const names = new Map(installations.map((i) => [i.id, i.display_name]));
    return (id: string) => names.get(id) ?? id;
  }, [installations]);

  return (
    <>
      <ErrorLine error={loadError} />
      <GroupsSection
        operatorKey={operatorKey}
        groups={groups}
        installations={installations}
        installationName={installationName}
        onChange={reload}
      />
      <PlansSection operatorKey={operatorKey} plans={plans} onChange={reload} />
      <RolloutsSection
        operatorKey={operatorKey}
        operator={operator}
        rollouts={rollouts}
        plans={plans}
        groups={groups}
        installations={installations}
        installationName={installationName}
        onChange={reload}
      />
      <OperatorsSection
        operatorKey={operatorKey}
        operator={operator}
        operators={operators}
        onChange={reload}
      />
    </>
  );
}

// --- Groups -------------------------------------------------------------------

function GroupsSection({
  operatorKey,
  groups,
  installations,
  installationName,
  onChange,
}: {
  operatorKey: string;
  groups: FleetGroup[];
  installations: ManagedInstallation[];
  installationName: (id: string) => string;
  onChange: () => Promise<void>;
}) {
  const { t } = useI18n();
  const { busy, error, run } = useAction(t("common.actionError"));
  const [name, setName] = useState("");
  const [toAdd, setToAdd] = useState<Record<string, string>>({});

  async function create(event: FormEvent) {
    event.preventDefault();
    if (await run(() => createFleetGroup(name.trim(), operatorKey))) {
      setName("");
      await onChange();
    }
  }

  async function act(action: () => Promise<unknown>) {
    if (await run(action)) await onChange();
  }

  return (
    <section className={card} aria-labelledby="fleet-groups-heading">
      <h2 id="fleet-groups-heading">{t("fleetOrchestration.groups.heading")}</h2>
      <p className="text-sm opacity-80">{t("fleetOrchestration.groups.hint")}</p>
      <ErrorLine error={error} />
      <form className="flex flex-wrap items-end gap-2" onSubmit={create}>
        <label className="flex flex-col gap-1 text-sm">
          {t("fleetOrchestration.groups.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} required className={fieldInput} />
        </label>
        <button type="submit" className={primaryBtn} disabled={busy || !name.trim()}>
          {t("fleetOrchestration.groups.create")}
        </button>
      </form>
      {groups.length === 0 ? (
        <p className="italic opacity-70">{t("fleetOrchestration.groups.empty")}</p>
      ) : (
        <div className="dms-scroll-x">
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("fleetOrchestration.groups.name")}</th>
                <th>{t("fleetOrchestration.groups.members")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => {
                const candidates = installations.filter((i) => !group.installation_ids.includes(i.id));
                const selected = toAdd[group.id] ?? "";
                return (
                  <tr key={group.id}>
                    <td className="whitespace-nowrap">{group.name}</td>
                    <td>
                      <div className="flex flex-wrap items-center gap-2">
                        {group.installation_ids.length === 0 && (
                          <span className="text-sm italic opacity-70">{t("fleetOrchestration.groups.noMembers")}</span>
                        )}
                        {group.installation_ids.map((id) => (
                          <span key={id} className="badge neutral inline-flex items-center gap-1">
                            {installationName(id)}
                            <button
                              type="button"
                              className="border-0 bg-transparent p-0 text-inherit"
                              aria-label={t("fleetOrchestration.groups.removeMember", {
                                name: installationName(id),
                                group: group.name,
                              })}
                              disabled={busy}
                              onClick={() => act(() => removeFleetGroupMember(group.id, id, operatorKey))}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                      {candidates.length > 0 && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          <select
                            aria-label={t("fleetOrchestration.groups.addLabel", { group: group.name })}
                            value={selected}
                            onChange={(e) => setToAdd((prev) => ({ ...prev, [group.id]: e.target.value }))}
                            className={fieldInput}
                          >
                            <option value="">{t("fleetOrchestration.groups.choose")}</option>
                            {candidates.map((i) => (
                              <option key={i.id} value={i.id}>
                                {i.display_name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            className={secondaryBtn}
                            disabled={busy || !selected}
                            onClick={async () => {
                              await act(() => addFleetGroupMember(group.id, selected, operatorKey));
                              setToAdd((prev) => ({ ...prev, [group.id]: "" }));
                            }}
                          >
                            {t("fleetOrchestration.groups.add")}
                          </button>
                        </div>
                      )}
                    </td>
                    <td>
                      <button
                        type="button"
                        className={secondaryBtn}
                        disabled={busy}
                        onClick={() => {
                          if (window.confirm(t("fleetOrchestration.groups.deleteConfirm", { name: group.name })))
                            act(() => deleteFleetGroup(group.id, operatorKey));
                        }}
                      >
                        {t("fleetManagement.delete")}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// --- Update plans ---------------------------------------------------------------

const emptyStep = (): FleetPlanStep => ({ name: "", step_type: "gate", requires_approval: false });

function PlansSection({
  operatorKey,
  plans,
  onChange,
}: {
  operatorKey: string;
  plans: FleetPlan[];
  onChange: () => Promise<void>;
}) {
  const { t } = useI18n();
  const { busy, error, setError, run } = useAction(t("common.actionError"));
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [steps, setSteps] = useState<FleetPlanStep[]>([emptyStep()]);

  function updateStep(index: number, patch: Partial<FleetPlanStep>) {
    setSteps((prev) => prev.map((step, i) => (i === index ? { ...step, ...patch } : step)));
  }

  function moveStep(index: number, delta: number) {
    setSteps((prev) => {
      const next = [...prev];
      const [step] = next.splice(index, 1);
      next.splice(index + delta, 0, step);
      return next;
    });
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (steps.some((s) => !s.name.trim())) {
      setError(t("fleetOrchestration.plans.stepNameRequired"));
      return;
    }
    // Approval only applies to gate steps on the server; don't send a flag
    // that would look meaningful on a verify step.
    const payload = {
      name: name.trim(),
      version: version.trim(),
      steps: steps.map((s) => ({
        name: s.name.trim(),
        step_type: s.step_type,
        requires_approval: s.step_type === "gate" && s.requires_approval,
      })),
    };
    if (await run(() => createFleetPlan(payload, operatorKey))) {
      setName("");
      setVersion("");
      setSteps([emptyStep()]);
      await onChange();
    }
  }

  return (
    <section className={card} aria-labelledby="fleet-plans-heading">
      <h2 id="fleet-plans-heading">{t("fleetOrchestration.plans.heading")}</h2>
      <p className="text-sm opacity-80">{t("fleetOrchestration.plans.hint")}</p>
      <ErrorLine error={error} />
      <form aria-label={t("fleetOrchestration.plans.formLabel")} onSubmit={create}>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm">
            {t("fleetOrchestration.plans.name")}
            <input value={name} onChange={(e) => setName(e.target.value)} required className={fieldInput} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("fleetOrchestration.plans.version")}
            <input
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              required
              className={`${fieldInput} w-28`}
            />
          </label>
        </div>
        <ol className="my-3 flex list-none flex-col gap-2 p-0">
          {steps.map((step, index) => (
            <li key={index} className="flex flex-wrap items-end gap-2">
              <span className="w-6 pb-2 text-sm opacity-70">{index + 1}.</span>
              <label className="flex flex-col gap-1 text-sm">
                {t("fleetOrchestration.plans.stepName")}
                <input
                  value={step.name}
                  onChange={(e) => updateStep(index, { name: e.target.value })}
                  className={fieldInput}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                {t("fleetOrchestration.plans.stepType")}
                <select
                  value={step.step_type}
                  onChange={(e) => updateStep(index, { step_type: e.target.value as FleetPlanStep["step_type"] })}
                  className={fieldInput}
                >
                  <option value="gate">{t("fleetOrchestration.stepTypes.gate")}</option>
                  <option value="verify">{t("fleetOrchestration.stepTypes.verify")}</option>
                </select>
              </label>
              {step.step_type === "gate" && (
                <label className="flex items-center gap-1 pb-2 text-sm">
                  <input
                    type="checkbox"
                    checked={step.requires_approval}
                    onChange={(e) => updateStep(index, { requires_approval: e.target.checked })}
                  />
                  {t("fleetOrchestration.plans.requiresApproval")}
                </label>
              )}
              <span className="flex gap-1">
                <button
                  type="button"
                  className={secondaryBtn}
                  disabled={index === 0}
                  aria-label={t("fleetOrchestration.plans.moveUp", { index: index + 1 })}
                  onClick={() => moveStep(index, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  className={secondaryBtn}
                  disabled={index === steps.length - 1}
                  aria-label={t("fleetOrchestration.plans.moveDown", { index: index + 1 })}
                  onClick={() => moveStep(index, 1)}
                >
                  ↓
                </button>
                <button
                  type="button"
                  className={secondaryBtn}
                  disabled={steps.length === 1}
                  aria-label={t("fleetOrchestration.plans.removeStep", { index: index + 1 })}
                  onClick={() => setSteps((prev) => prev.filter((_, i) => i !== index))}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ol>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={secondaryBtn} onClick={() => setSteps((prev) => [...prev, emptyStep()])}>
            {t("fleetOrchestration.plans.addStep")}
          </button>
          <button type="submit" className={primaryBtn} disabled={busy}>
            {t("fleetOrchestration.plans.create")}
          </button>
        </div>
      </form>
      {plans.length === 0 ? (
        <p className="italic opacity-70">{t("fleetOrchestration.plans.empty")}</p>
      ) : (
        <ul className="mt-4 flex list-none flex-col gap-3 p-0">
          {plans.map((plan) => (
            <li key={plan.id} className="rounded-md border border-border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong>
                  {plan.name} <span className="font-normal opacity-70">v{plan.version}</span>
                </strong>
                <button
                  type="button"
                  className={secondaryBtn}
                  disabled={busy}
                  onClick={async () => {
                    if (!window.confirm(t("fleetOrchestration.plans.deleteConfirm", { name: plan.name }))) return;
                    if (await run(() => deleteFleetPlan(plan.id, operatorKey))) await onChange();
                  }}
                >
                  {t("fleetManagement.delete")}
                </button>
              </div>
              <ol className="mb-0 mt-2 pl-6 text-sm">
                {plan.steps.map((step, index) => (
                  <li key={index}>
                    {step.name}{" "}
                    <span className="opacity-70">
                      ({t(`fleetOrchestration.stepTypes.${step.step_type}`)}
                      {step.requires_approval && `, ${t("fleetOrchestration.plans.requiresApproval")}`})
                    </span>
                  </li>
                ))}
              </ol>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// --- Rollouts -------------------------------------------------------------------

type OpenForm = { key: string; kind: "mark-done" | "reject" } | null;

function RolloutsSection({
  operatorKey,
  operator,
  rollouts,
  plans,
  groups,
  installations,
  installationName,
  onChange,
}: {
  operatorKey: string;
  operator: FleetOperator;
  rollouts: FleetRollout[];
  plans: FleetPlan[];
  groups: FleetGroup[];
  installations: ManagedInstallation[];
  installationName: (id: string) => string;
  onChange: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const { busy, error, setError, run } = useAction(t("common.actionError"));
  const [name, setName] = useState("");
  const [planId, setPlanId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [include, setInclude] = useState<string[]>([]);
  const [exclude, setExclude] = useState<string[]>([]);
  const [openForm, setOpenForm] = useState<OpenForm>(null);
  const [outcome, setOutcome] = useState<FleetGateOutcome>("success");
  const [detail, setDetail] = useState("");

  const planById = useMemo(() => new Map(plans.map((p) => [p.id, p])), [plans]);
  const groupMembers = groups.find((g) => g.id === groupId)?.installation_ids ?? [];
  // The same resolution the server does: group members ∪ include − exclude.
  const target = new Set([...groupMembers, ...include]);
  exclude.forEach((id) => target.delete(id));

  function toggle(list: string[], setList: (next: string[]) => void, id: string) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  async function create(event: FormEvent) {
    event.preventDefault();
    if (target.size === 0) {
      setError(t("fleetOrchestration.rollouts.emptyTarget"));
      return;
    }
    const payload = {
      plan_id: planId,
      name: name.trim(),
      group_id: groupId || null,
      include: include.filter((id) => !groupMembers.includes(id)),
      exclude: exclude.filter((id) => groupMembers.includes(id)),
    };
    if (await run(() => createFleetRollout(payload, operatorKey))) {
      setName("");
      setGroupId("");
      setInclude([]);
      setExclude([]);
      await onChange();
    }
  }

  async function act(action: () => Promise<unknown>) {
    if (await run(action)) {
      setOpenForm(null);
      setDetail("");
      setOutcome("success");
      await onChange();
    }
  }

  function runAction(rollout: FleetRollout, item: FleetRun, action: FleetRunAction, body?: Record<string, unknown>) {
    return act(() => fleetRunAction(rollout.id, item.installation_id, action, operatorKey, body));
  }

  const formatDate = (value: string | null) => (value ? new Date(value).toLocaleString(locale) : "—");
  const others = installations.filter((i) => !groupMembers.includes(i.id));

  return (
    <section className={card} aria-labelledby="fleet-rollouts-heading">
      <h2 id="fleet-rollouts-heading">{t("fleetOrchestration.rollouts.heading")}</h2>
      <p className="text-sm opacity-80">{t("fleetOrchestration.rollouts.hint")}</p>
      <ErrorLine error={error} />
      {plans.length === 0 ? (
        <p className="text-sm italic opacity-70">{t("fleetOrchestration.rollouts.needPlan")}</p>
      ) : (
        <form aria-label={t("fleetOrchestration.rollouts.formLabel")} onSubmit={create}>
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1 text-sm">
              {t("fleetOrchestration.rollouts.name")}
              <input value={name} onChange={(e) => setName(e.target.value)} required className={fieldInput} />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("fleetOrchestration.rollouts.plan")}
              <select value={planId} onChange={(e) => setPlanId(e.target.value)} required className={fieldInput}>
                <option value="">{t("fleetOrchestration.groups.choose")}</option>
                {plans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} v{p.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t("fleetOrchestration.rollouts.group")}
              <select
                value={groupId}
                onChange={(e) => {
                  setGroupId(e.target.value);
                  setExclude([]);
                }}
                className={fieldInput}
              >
                <option value="">{t("fleetOrchestration.rollouts.noGroup")}</option>
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {groupMembers.length > 0 && (
            <fieldset className="mt-3 rounded-md border border-border p-2">
              <legend className="px-1 text-sm">{t("fleetOrchestration.rollouts.exclude")}</legend>
              <div className="flex flex-wrap gap-3">
                {groupMembers.map((id) => (
                  <label key={id} className="flex items-center gap-1 text-sm">
                    <input type="checkbox" checked={exclude.includes(id)} onChange={() => toggle(exclude, setExclude, id)} />
                    {installationName(id)}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          {others.length > 0 && (
            <fieldset className="mt-3 rounded-md border border-border p-2">
              <legend className="px-1 text-sm">
                {groupId ? t("fleetOrchestration.rollouts.includeExtra") : t("fleetOrchestration.rollouts.include")}
              </legend>
              <div className="flex flex-wrap gap-3">
                {others.map((i) => (
                  <label key={i.id} className="flex items-center gap-1 text-sm">
                    <input type="checkbox" checked={include.includes(i.id)} onChange={() => toggle(include, setInclude, i.id)} />
                    {i.display_name}
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button type="submit" className={primaryBtn} disabled={busy}>
              {t("fleetOrchestration.rollouts.create")}
            </button>
            <span className="text-sm opacity-80">{t("fleetOrchestration.rollouts.targetCount", { count: target.size })}</span>
          </div>
        </form>
      )}

      {rollouts.length === 0 ? (
        <p className="italic opacity-70">{t("fleetOrchestration.rollouts.empty")}</p>
      ) : (
        <ul className="mt-4 flex list-none flex-col gap-4 p-0">
          {rollouts.map((rollout) => {
            const plan = planById.get(rollout.plan_id);
            return (
              <li key={rollout.id} className="rounded-md border border-border p-3" aria-label={rollout.name}>
                <div className="flex flex-wrap items-center gap-2">
                  <strong>{rollout.name}</strong>
                  <span className={`badge ${ROLLOUT_BADGE[rollout.status]}`}>
                    {t(`fleetOrchestration.rolloutStates.${rollout.status}`)}
                  </span>
                  {plan && (
                    <span className="text-sm opacity-70">
                      {plan.name} v{plan.version}
                    </span>
                  )}
                  {rollout.started_by && (
                    <span className="text-sm opacity-70">
                      {t("fleetOrchestration.rollouts.startedBy", {
                        name: rollout.started_by,
                        date: formatDate(rollout.started_at),
                      })}
                    </span>
                  )}
                  <span className="ml-auto flex gap-2">
                    {rollout.status === "draft" && (
                      <button
                        type="button"
                        className={primaryBtn}
                        disabled={busy}
                        onClick={() => act(() => startFleetRollout(rollout.id, operatorKey))}
                      >
                        {t("fleetOrchestration.rollouts.start")}
                      </button>
                    )}
                    {rollout.status === "running" && (
                      <button
                        type="button"
                        className={secondaryBtn}
                        disabled={busy}
                        onClick={() => {
                          if (window.confirm(t("fleetOrchestration.rollouts.cancelConfirm", { name: rollout.name })))
                            act(() => cancelFleetRollout(rollout.id, operatorKey));
                        }}
                      >
                        {t("fleetOrchestration.rollouts.cancel")}
                      </button>
                    )}
                    {rollout.status !== "running" && (
                      <button
                        type="button"
                        className={secondaryBtn}
                        disabled={busy}
                        onClick={() => {
                          if (window.confirm(t("fleetOrchestration.rollouts.deleteConfirm", { name: rollout.name })))
                            act(() => deleteFleetRollout(rollout.id, operatorKey));
                        }}
                      >
                        {t("fleetManagement.delete")}
                      </button>
                    )}
                  </span>
                </div>
                <div className="dms-scroll-x mt-2">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>{t("fleetOrchestration.rollouts.installation")}</th>
                        <th>{t("fleetOrchestration.rollouts.step")}</th>
                        <th>{t("fleetManagement.status")}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {rollout.runs.map((item) => {
                        const step = plan?.steps[item.current_step_index];
                        const formKey = `${rollout.id}:${item.installation_id}`;
                        const ownProposal = item.proposed_by === operator.name;
                        return (
                          <Fragment key={item.id}>
                            <tr>
                              <td className="whitespace-nowrap">{item.installation_display_name}</td>
                              <td>
                                {item.current_step_name ? (
                                  <>
                                    {t("fleetOrchestration.rollouts.stepOf", {
                                      index: item.current_step_index + 1,
                                      total: plan?.steps.length ?? "?",
                                    })}{" "}
                                    {item.current_step_name}
                                    {step && (
                                      <span className="opacity-70"> ({t(`fleetOrchestration.stepTypes.${step.step_type}`)})</span>
                                    )}
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                              <td>
                                <span className={`badge ${RUN_BADGE[item.status]}`}>
                                  {t(`fleetOrchestration.runStates.${item.status}`)}
                                </span>
                                {item.proposed_by && item.status === "manual_required" && (
                                  <div className="text-sm opacity-80">
                                    {t("fleetOrchestration.rollouts.proposedBy", { name: item.proposed_by })}
                                  </div>
                                )}
                                {item.error_message && <div className="text-sm text-danger">{item.error_message}</div>}
                              </td>
                              <td>
                                {/* Hidden while this run's form is open: the form's
                                    own submit button takes over. */}
                                {rollout.status === "running" && openForm?.key !== formKey && (
                                  <div className="flex flex-wrap gap-2">
                                    {step?.step_type === "verify" &&
                                      (item.status === "wait_external" || item.status === "retry_later") && (
                                        <button
                                          type="button"
                                          className={primaryBtn}
                                          disabled={busy}
                                          onClick={() => runAction(rollout, item, "advance")}
                                        >
                                          {t("fleetOrchestration.actions.verify")}
                                        </button>
                                      )}
                                    {step?.step_type === "gate" && item.status === "wait_external" && (
                                      <button
                                        type="button"
                                        className={primaryBtn}
                                        disabled={busy}
                                        onClick={() => setOpenForm({ key: formKey, kind: "mark-done" })}
                                      >
                                        {t("fleetOrchestration.actions.markDone")}
                                      </button>
                                    )}
                                    {(item.status === "recoverable_failed" ||
                                      (item.status === "retry_later" && step?.step_type === "gate")) && (
                                      <button
                                        type="button"
                                        className={secondaryBtn}
                                        disabled={busy}
                                        onClick={() => runAction(rollout, item, "retry")}
                                      >
                                        {t("fleetOrchestration.actions.retry")}
                                      </button>
                                    )}
                                    {item.status === "manual_required" && (
                                      <>
                                        <button
                                          type="button"
                                          className={primaryBtn}
                                          disabled={busy || ownProposal}
                                          title={ownProposal ? t("fleetOrchestration.rollouts.ownProposal") : undefined}
                                          onClick={() => runAction(rollout, item, "approve")}
                                        >
                                          {t("fleetOrchestration.actions.approve")}
                                        </button>
                                        <button
                                          type="button"
                                          className={secondaryBtn}
                                          disabled={busy}
                                          onClick={() => setOpenForm({ key: formKey, kind: "reject" })}
                                        >
                                          {t("fleetOrchestration.actions.reject")}
                                        </button>
                                      </>
                                    )}
                                    {item.status === "fatal_contract" && (
                                      <button
                                        type="button"
                                        className={secondaryBtn}
                                        disabled={busy}
                                        onClick={() => {
                                          if (window.confirm(t("fleetOrchestration.actions.acknowledgeConfirm")))
                                            runAction(rollout, item, "acknowledge-fatal");
                                        }}
                                      >
                                        {t("fleetOrchestration.actions.acknowledge")}
                                      </button>
                                    )}
                                  </div>
                                )}
                                {item.status === "manual_required" && ownProposal && (
                                  <p className="mb-0 mt-1 text-sm opacity-80">{t("fleetOrchestration.rollouts.ownProposal")}</p>
                                )}
                              </td>
                            </tr>
                            {openForm?.key === formKey && (
                              <tr className="bg-hover-bg">
                                <td colSpan={4}>
                                  <form
                                    aria-label={t(
                                      openForm.kind === "reject"
                                        ? "fleetOrchestration.actions.rejectFormLabel"
                                        : "fleetOrchestration.actions.markDoneFormLabel",
                                      { name: item.installation_display_name }
                                    )}
                                    className="flex flex-wrap items-end gap-2"
                                    onSubmit={(event) => {
                                      event.preventDefault();
                                      if (openForm.kind === "reject") runAction(rollout, item, "reject", { reason: detail || null });
                                      else runAction(rollout, item, "mark-done", { outcome, detail: detail || null });
                                    }}
                                  >
                                    {openForm.kind === "mark-done" && (
                                      <label className="flex flex-col gap-1 text-sm">
                                        {t("fleetOrchestration.actions.outcome")}
                                        <select
                                          value={outcome}
                                          onChange={(e) => setOutcome(e.target.value as FleetGateOutcome)}
                                          className={fieldInput}
                                        >
                                          {GATE_OUTCOMES.map((o) => (
                                            <option key={o} value={o}>
                                              {t(`fleetOrchestration.outcomes.${o}`)}
                                            </option>
                                          ))}
                                        </select>
                                      </label>
                                    )}
                                    <label className="flex flex-col gap-1 text-sm">
                                      {openForm.kind === "reject"
                                        ? t("fleetOrchestration.actions.reason")
                                        : t("fleetOrchestration.actions.detail")}
                                      <input value={detail} onChange={(e) => setDetail(e.target.value)} className={fieldInput} />
                                    </label>
                                    <button type="submit" className={primaryBtn} disabled={busy}>
                                      {openForm.kind === "reject"
                                        ? t("fleetOrchestration.actions.reject")
                                        : t("fleetOrchestration.actions.report")}
                                    </button>
                                    <button type="button" className={secondaryBtn} onClick={() => setOpenForm(null)}>
                                      {t("common.cancel")}
                                    </button>
                                  </form>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// --- Operators ------------------------------------------------------------------

function OperatorsSection({
  operatorKey,
  operator,
  operators,
  onChange,
}: {
  operatorKey: string;
  operator: FleetOperator;
  operators: FleetOperator[];
  onChange: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const { busy, error, run } = useAction(t("common.actionError"));
  const [name, setName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [minted, setMinted] = useState<{ name: string; token: string } | null>(null);

  async function create(event: FormEvent) {
    event.preventDefault();
    const ok = await run(async () => {
      const created = await createFleetOperator(name.trim(), isAdmin, operatorKey);
      setMinted({ name: created.name, token: created.token });
    });
    if (ok) {
      setName("");
      setIsAdmin(false);
      await onChange();
    }
  }

  return (
    <section className={card} aria-labelledby="fleet-operators-heading">
      <h2 id="fleet-operators-heading">{t("fleetOrchestration.operators.heading")}</h2>
      <p className="text-sm opacity-80">{t("fleetOrchestration.operators.hint")}</p>
      <ErrorLine error={error} />
      {minted && (
        <div className="text-sm" role="alert">
          <p>{t("fleetOrchestration.operators.tokenWarning", { name: minted.name })}</p>
          <code className="rounded bg-hover-bg px-1.5 py-0.5 text-xs">{minted.token}</code>
          <button type="button" onClick={() => setMinted(null)} className={`${secondaryBtn} ml-2`}>
            {t("fleetManagement.dismiss")}
          </button>
        </div>
      )}
      {operator.is_admin && (
        <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={create}>
          <label className="flex flex-col gap-1 text-sm">
            {t("fleetOrchestration.operators.name")}
            <input value={name} onChange={(e) => setName(e.target.value)} required className={fieldInput} />
          </label>
          <label className="flex items-center gap-1 pb-2 text-sm">
            <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
            {t("fleetManagement.adminOperator")}
          </label>
          <button type="submit" className={primaryBtn} disabled={busy || !name.trim()}>
            {t("fleetOrchestration.operators.create")}
          </button>
        </form>
      )}
      <div className="dms-scroll-x">
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("fleetOrchestration.operators.name")}</th>
              <th>{t("fleetOrchestration.operators.role")}</th>
              <th>{t("fleetOrchestration.operators.createdAt")}</th>
              <th>{t("fleetManagement.status")}</th>
              {operator.is_admin && <th />}
            </tr>
          </thead>
          <tbody>
            {operators.map((o) => (
              <tr key={o.id}>
                <td className="whitespace-nowrap">
                  {o.name}
                  {o.id === operator.id && <span className="opacity-70"> ({t("fleetOrchestration.operators.you")})</span>}
                </td>
                <td>{o.is_admin ? t("fleetManagement.adminOperator") : t("fleetOrchestration.operators.operator")}</td>
                <td className="whitespace-nowrap">{new Date(o.created_at).toLocaleString(locale)}</td>
                <td>
                  <span className={`badge ${o.revoked_at ? "down" : "ok"}`}>
                    {o.revoked_at ? t("fleetOrchestration.operators.revoked") : t("fleetOrchestration.operators.active")}
                  </span>
                </td>
                {operator.is_admin && (
                  <td>
                    {/* bootstrap is the env key's identity: revoked by
                        rotating the env value, never through the API. */}
                    {!o.revoked_at && o.name !== "bootstrap" && (
                      <button
                        type="button"
                        className={secondaryBtn}
                        disabled={busy}
                        onClick={async () => {
                          if (!window.confirm(t("fleetOrchestration.operators.revokeConfirm", { name: o.name }))) return;
                          if (await run(() => revokeFleetOperator(o.id, operatorKey))) await onChange();
                        }}
                      >
                        {t("fleetOrchestration.operators.revoke")}
                      </button>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
