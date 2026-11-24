"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useI18n } from "@/i18n";
import {
  addRouteStep,
  ApiError,
  createCase,
  getCirculationRoute,
  listProcessDefinitions,
  lookupUserByUsername,
  recallRouteStep,
  removeRouteStep,
  reorderRoute,
  type CirculationRoute,
  type ProcessDefinition,
  type RouteEntry,
  type RouteKind,
  type RouteStep,
} from "@/lib/api";
import { principalName, usePrincipalNames } from "@/lib/usePrincipalNames";
import { UiIcon } from "@/lib/ui-icons";

// Circulation folders with an editable route (P93-S3, ADR 0249 Part A b):
// the route editor in a case's detail, and "Neue Umlaufmappe" on the list.
// The route is data in workflow-service - adding a co-signer, striking a
// step or sending the folder back to the author never changes the model.

const KINDS: RouteKind[] = ["mitzeichnen", "zeichnen", "kenntnis", "bearbeiten"];
const MIN_REASON = 3;

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const iconBtn =
  "inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-hover-bg text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-40";
const fieldInput =
  "box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

// --- One route entry: kind, person or group, note -------------------------

interface EntryDraft {
  kind: RouteKind;
  assigneeType: "principal" | "group";
  assignee: string; // a username for a person, a name for a group
  note: string;
}

const emptyDraft = (kind: RouteKind = "mitzeichnen"): EntryDraft => ({
  kind,
  assigneeType: "principal",
  assignee: "",
  note: "",
});

// A person is typed as a username and stored as their principal id.
async function resolveDraft(token: string, draft: EntryDraft): Promise<RouteEntry> {
  const assignee =
    draft.assigneeType === "principal"
      ? (await lookupUserByUsername(token, draft.assignee.trim())).id
      : draft.assignee.trim();
  return {
    kind: draft.kind,
    assignee_type: draft.assigneeType,
    assignee,
    note: draft.note.trim() || null,
  };
}

function EntryFields({
  draft,
  onChange,
  idPrefix,
}: {
  draft: EntryDraft;
  onChange: (draft: EntryDraft) => void;
  idPrefix: string;
}) {
  const { t } = useI18n();
  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,8rem)_minmax(0,1fr)]">
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${idPrefix}-kind`}>
        {t("circulation.kind")}
        <select
          id={`${idPrefix}-kind`}
          className={fieldInput}
          value={draft.kind}
          onChange={(e) => onChange({ ...draft, kind: e.target.value as RouteKind })}
        >
          {KINDS.map((kind) => (
            <option key={kind} value={kind}>
              {t(`circulation.kinds.${kind}`)}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${idPrefix}-type`}>
        {t("circulation.assigneeType")}
        <select
          id={`${idPrefix}-type`}
          className={fieldInput}
          value={draft.assigneeType}
          onChange={(e) =>
            onChange({ ...draft, assigneeType: e.target.value as "principal" | "group" })
          }
        >
          <option value="principal">{t("circulation.person")}</option>
          <option value="group">{t("circulation.group")}</option>
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor={`${idPrefix}-assignee`}>
        {draft.assigneeType === "principal"
          ? t("circulation.username")
          : t("circulation.groupName")}
        <input
          id={`${idPrefix}-assignee`}
          className={fieldInput}
          value={draft.assignee}
          onChange={(e) => onChange({ ...draft, assignee: e.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm sm:col-span-3" htmlFor={`${idPrefix}-note`}>
        {t("circulation.note")}
        <input
          id={`${idPrefix}-note`}
          className={fieldInput}
          value={draft.note}
          onChange={(e) => onChange({ ...draft, note: e.target.value })}
        />
      </label>
    </div>
  );
}

function useAssigneeLabel(token: string, ids: string[]) {
  const { t } = useI18n();
  const names = usePrincipalNames(token, ids);
  return useCallback(
    (entry: { assignee_type: string; assignee: string }) =>
      entry.assignee_type === "group"
        ? t("circulation.groupLabel", { name: entry.assignee })
        : principalName(names, entry.assignee),
    [names, t]
  );
}

// --- The route in a case's detail -----------------------------------------

type Redirect = "author" | `step:${string}` | "other";

export function CirculationRouteSection({
  token,
  instanceId,
  onChanged,
}: {
  token: string;
  instanceId: string;
  onChanged?: () => void;
}) {
  const { t, locale } = useI18n();
  const [route, setRoute] = useState<CirculationRoute | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [addDraft, setAddDraft] = useState<EntryDraft>(emptyDraft());
  const [addAt, setAddAt] = useState<"next" | "end">("next");
  const [recallOpen, setRecallOpen] = useState(false);
  const [redirect, setRedirect] = useState<Redirect>("author");
  const [redirectDraft, setRedirectDraft] = useState<EntryDraft>(emptyDraft("bearbeiten"));

  const reload = useCallback(async () => {
    try {
      setRoute(await getCirculationRoute(token, instanceId));
    } catch {
      // Not a circulation (404) or not reachable: no route section.
      setRoute(null);
    }
  }, [token, instanceId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const ids = useMemo(
    () =>
      route
        ? [route.created_by, ...route.steps.flatMap((s) => [s.assignee, s.closed_by ?? ""])].filter(
            Boolean
          )
        : [],
    [route]
  );
  const label = useAssigneeLabel(token, ids);
  const names = usePrincipalNames(token, ids);

  if (!route) return null;

  const steps = route.steps;
  const pending = steps.filter((s) => s.status === "pending");
  const current = steps.find((s) => s.status === "current");
  const editable = route.can_edit && route.instance_status === "running";
  const reasonOk = reason.trim().length >= MIN_REASON;
  const earlier = steps
    .filter((s) => s.status === "done" && s.assignee !== route.created_by)
    .filter((s, i, all) => all.findIndex((o) => o.assignee === s.assignee) === i);

  async function change(action: () => Promise<CirculationRoute>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setRoute(await action());
      setReason("");
      setNotice(done);
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("circulation.changeError"));
      if (err instanceof ApiError && err.status === 409) await reload();
    } finally {
      setBusy(false);
    }
  }

  const version = route.workflow_version;

  function move(step: RouteStep, delta: -1 | 1) {
    const order = pending.map((s) => s.id);
    const from = order.indexOf(step.id);
    const to = from + delta;
    if (to < 0 || to >= order.length) return;
    [order[from], order[to]] = [order[to], order[from]];
    return change(
      () => reorderRoute(token, instanceId, { stepIds: order, reason, expectedVersion: version }),
      t("circulation.reordered")
    );
  }

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    if (!addDraft.assignee.trim()) return;
    await change(async () => {
      const step = await resolveDraft(token, addDraft);
      const result = await addRouteStep(token, instanceId, {
        step,
        index: addAt === "next" ? 0 : null,
        reason,
        expectedVersion: version,
      });
      setAddDraft(emptyDraft());
      return result;
    }, t("circulation.added"));
  }

  async function handleRecall(e: React.FormEvent) {
    e.preventDefault();
    await change(async () => {
      let entry: RouteEntry;
      if (redirect === "author") {
        entry = {
          kind: redirectDraft.kind,
          assignee_type: "principal",
          assignee: route!.created_by,
          note: redirectDraft.note.trim() || null,
        };
      } else if (redirect.startsWith("step:")) {
        const source = steps.find((s) => s.id === redirect.slice(5))!;
        entry = {
          kind: redirectDraft.kind,
          assignee_type: source.assignee_type,
          assignee: source.assignee,
          note: redirectDraft.note.trim() || null,
        };
      } else {
        entry = await resolveDraft(token, redirectDraft);
      }
      const result = await recallRouteStep(token, instanceId, {
        redirect: entry,
        reason,
        expectedVersion: version,
      });
      setRecallOpen(false);
      setRedirectDraft(emptyDraft("bearbeiten"));
      return result;
    }, t("circulation.recalled"));
  }

  function statusText(step: RouteStep): string {
    const when = step.closed_at ? new Date(step.closed_at).toLocaleString(locale) : "";
    const who = principalName(names, step.closed_by);
    switch (step.status) {
      case "done":
        return t("circulation.status.done", { who, when });
      case "current":
        return t("circulation.status.current");
      case "pending":
        return t("circulation.status.pending");
      case "removed":
        return t("circulation.status.removed", { reason: step.close_reason ?? "" });
      default:
        return t("circulation.status.recalled", { reason: step.close_reason ?? "" });
    }
  }

  let number = 0;
  return (
    <section aria-label={t("circulation.heading")} className="flex flex-col gap-3">
      <h3 className="m-0">{t("circulation.heading")}</h3>
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {steps.map((step) => {
          const struck = step.status === "removed" || step.status === "recalled";
          if (!struck) number += 1;
          const pendingIndex = pending.findIndex((p) => p.id === step.id);
          return (
            <li
              key={step.id}
              data-status={step.status}
              className={`flex flex-wrap items-start justify-between gap-2 rounded-md border px-3 py-2 ${
                step.status === "current" ? "border-accent bg-accent-bg" : "border-border"
              } ${struck ? "opacity-60" : ""}`}
            >
              <div className="flex min-w-0 flex-col">
                <span className={struck ? "line-through" : undefined}>
                  {struck ? "–" : `${number}.`} {t(`circulation.kinds.${step.kind}`)}:{" "}
                  <strong>{label(step)}</strong>
                </span>
                {step.note && <span className="text-sm opacity-80">{step.note}</span>}
                <span className="text-xs opacity-70">{statusText(step)}</span>
              </div>
              {editable && step.status === "pending" && (
                <div className="flex gap-1">
                  <button
                    type="button"
                    className={iconBtn}
                    aria-label={t("circulation.moveUp", { name: label(step) })}
                    title={t("circulation.moveUp", { name: label(step) })}
                    disabled={busy || !reasonOk || pendingIndex === 0}
                    onClick={() => move(step, -1)}
                  >
                    <UiIcon icon={ArrowUp} />
                  </button>
                  <button
                    type="button"
                    className={iconBtn}
                    aria-label={t("circulation.moveDown", { name: label(step) })}
                    title={t("circulation.moveDown", { name: label(step) })}
                    disabled={busy || !reasonOk || pendingIndex === pending.length - 1}
                    onClick={() => move(step, 1)}
                  >
                    <UiIcon icon={ArrowDown} />
                  </button>
                  <button
                    type="button"
                    className={iconBtn}
                    aria-label={t("circulation.remove", { name: label(step) })}
                    title={t("circulation.remove", { name: label(step) })}
                    disabled={busy || !reasonOk}
                    onClick={() =>
                      change(
                        () =>
                          removeRouteStep(token, instanceId, step.id, {
                            reason,
                            expectedVersion: version,
                          }),
                        t("circulation.removed")
                      )
                    }
                  >
                    <UiIcon icon={X} />
                  </button>
                </div>
              )}
              {editable && step.status === "current" && (
                <button
                  type="button"
                  className={secondaryBtn}
                  aria-expanded={recallOpen}
                  onClick={() => setRecallOpen((open) => !open)}
                >
                  {t("circulation.recall")}
                </button>
              )}
            </li>
          );
        })}
      </ol>

      {route.instance_status !== "running" && (
        <p className="m-0 text-sm italic opacity-70">{t("circulation.finished")}</p>
      )}
      {error && (
        <p className="m-0 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="m-0 rounded-md bg-accent-bg px-3 py-2 text-sm" role="status">
          {notice}
        </p>
      )}

      {editable && (
        <div className="flex flex-col gap-3 rounded-md border border-border p-3">
          <div className="flex flex-col gap-1 text-sm">
            <label className="flex flex-col gap-1" htmlFor="circulation-reason">
              {t("circulation.reason")}
            </label>
            <input
              id="circulation-reason"
              className={fieldInput}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={t("circulation.reasonPlaceholder")}
              aria-describedby="circulation-reason-hint"
            />
            <span id="circulation-reason-hint" className="text-xs opacity-70">
              {t("circulation.reasonHint")}
            </span>
          </div>

          {recallOpen && current && (
            <form
              aria-label={t("circulation.recallHeading")}
              className="flex flex-col gap-2"
              onSubmit={handleRecall}
            >
              <h4 className="m-0 text-sm">{t("circulation.recallHeading")}</h4>
              <label className="flex flex-col gap-1 text-sm" htmlFor="circulation-redirect">
                {t("circulation.redirectTo")}
                <select
                  id="circulation-redirect"
                  className={fieldInput}
                  value={redirect}
                  onChange={(e) => {
                    const value = e.target.value as Redirect;
                    setRedirect(value);
                    setRedirectDraft(emptyDraft(value === "author" ? "bearbeiten" : current.kind));
                  }}
                >
                  <option value="author">
                    {t("circulation.toAuthor", {
                      name: principalName(names, route.created_by),
                    })}
                  </option>
                  {earlier.map((s) => (
                    <option key={s.id} value={`step:${s.id}`}>
                      {label(s)}
                    </option>
                  ))}
                  <option value="other">{t("circulation.toOther")}</option>
                </select>
              </label>
              {redirect === "other" ? (
                <EntryFields
                  draft={redirectDraft}
                  onChange={setRedirectDraft}
                  idPrefix="circulation-redirect-entry"
                />
              ) : (
                <div className="grid gap-2 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
                  <label
                    className="flex flex-col gap-1 text-sm"
                    htmlFor="circulation-redirect-kind"
                  >
                    {t("circulation.kind")}
                    <select
                      id="circulation-redirect-kind"
                      className={fieldInput}
                      value={redirectDraft.kind}
                      onChange={(e) =>
                        setRedirectDraft({ ...redirectDraft, kind: e.target.value as RouteKind })
                      }
                    >
                      {KINDS.map((kind) => (
                        <option key={kind} value={kind}>
                          {t(`circulation.kinds.${kind}`)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label
                    className="flex flex-col gap-1 text-sm"
                    htmlFor="circulation-redirect-note"
                  >
                    {t("circulation.note")}
                    <input
                      id="circulation-redirect-note"
                      className={fieldInput}
                      value={redirectDraft.note}
                      onChange={(e) => setRedirectDraft({ ...redirectDraft, note: e.target.value })}
                    />
                  </label>
                </div>
              )}
              <div>
                <button
                  type="submit"
                  className={primaryBtn}
                  disabled={
                    busy || !reasonOk || (redirect === "other" && !redirectDraft.assignee.trim())
                  }
                >
                  {t("circulation.recallSubmit")}
                </button>
              </div>
            </form>
          )}

          <form
            aria-label={t("circulation.addHeading")}
            className="flex flex-col gap-2"
            onSubmit={handleAdd}
          >
            <h4 className="m-0 text-sm">{t("circulation.addHeading")}</h4>
            <EntryFields draft={addDraft} onChange={setAddDraft} idPrefix="circulation-add" />
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-sm" htmlFor="circulation-add-at">
                {t("circulation.addAt")}
                <select
                  id="circulation-add-at"
                  className={fieldInput}
                  value={addAt}
                  onChange={(e) => setAddAt(e.target.value as "next" | "end")}
                >
                  <option value="next">{t("circulation.addNext")}</option>
                  <option value="end">{t("circulation.addEnd")}</option>
                </select>
              </label>
              <button
                type="submit"
                className={primaryBtn}
                disabled={busy || !reasonOk || !addDraft.assignee.trim()}
              >
                {t("circulation.add")}
              </button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

// --- "Neue Umlaufmappe" ----------------------------------------------------

interface PlannedStep {
  entry: RouteEntry;
  display: string;
}

export function NewCirculationSection({
  token,
  onCaseCreated,
}: {
  token: string;
  onCaseCreated: (caseId: string) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [definitions, setDefinitions] = useState<ProcessDefinition[]>([]);
  const [definitionId, setDefinitionId] = useState("");
  const [name, setName] = useState("");
  const [planned, setPlanned] = useState<PlannedStep[]>([]);
  const [draft, setDraft] = useState<EntryDraft>(emptyDraft());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !token) return;
    listProcessDefinitions(token)
      .then((defs) => {
        setDefinitions(defs);
        // The shipped template first, then any other circulation definition.
        const preferred =
          defs.find((d) => d.is_circulation && d.name === "Umlauf") ??
          defs.find((d) => d.is_circulation) ??
          defs[0];
        setDefinitionId((current) => current || (preferred ? String(preferred.id) : ""));
      })
      .catch(() => setDefinitions([]));
  }, [open, token]);

  const definition = definitions.find((d) => String(d.id) === definitionId);
  const needsRoute = Boolean(definition?.is_circulation);

  async function addPlanned() {
    if (!draft.assignee.trim()) return;
    setError(null);
    try {
      const entry = await resolveDraft(token, draft);
      const who =
        draft.assigneeType === "group"
          ? t("circulation.groupLabel", { name: entry.assignee })
          : draft.assignee.trim();
      setPlanned((list) => [...list, { entry, display: who }]);
      setDraft(emptyDraft());
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 404
          ? t("circulation.userNotFound", { name: draft.assignee.trim() })
          : err instanceof ApiError
            ? err.message
            : t("circulation.changeError")
      );
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !definition) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createCase(token, {
        name: name.trim(),
        processDefinitionId: definition.id,
        route: needsRoute ? planned.map((p) => p.entry) : undefined,
      });
      setOpen(false);
      setName("");
      setPlanned([]);
      onCaseCreated(created.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("circulation.createError"));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-4">
        <button type="button" className={primaryBtn} onClick={() => setOpen(true)}>
          {t("circulation.newCase")}
        </button>
      </div>
    );
  }

  return (
    <form
      aria-label={t("circulation.newCase")}
      className="mt-4 flex flex-col gap-3 rounded-md border border-border p-3"
      onSubmit={handleCreate}
    >
      <h3 className="m-0">{t("circulation.newCase")}</h3>
      <label className="flex flex-col gap-1 text-sm" htmlFor="new-circulation-name">
        {t("circulation.caseName")}
        <input
          id="new-circulation-name"
          className={fieldInput}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm" htmlFor="new-circulation-process">
        {t("circulation.process")}
        <select
          id="new-circulation-process"
          className={fieldInput}
          value={definitionId}
          onChange={(e) => setDefinitionId(e.target.value)}
        >
          {definitions.map((d) => (
            <option key={d.id} value={d.id}>
              {d.is_circulation
                ? t("circulation.processCirculation", { name: d.name, version: d.version })
                : `${d.name} v${d.version}`}
            </option>
          ))}
        </select>
      </label>

      {needsRoute && (
        <div className="flex flex-col gap-2">
          <h4 className="m-0 text-sm">{t("circulation.initialRoute")}</h4>
          {planned.length === 0 ? (
            <p className="m-0 text-sm italic opacity-70">{t("circulation.noStepsYet")}</p>
          ) : (
            <ol className="m-0 flex list-none flex-col gap-1 p-0">
              {planned.map((p, index) => (
                <li
                  key={`${p.entry.assignee}-${index}`}
                  className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-1.5 text-sm"
                >
                  <span>
                    {index + 1}. {t(`circulation.kinds.${p.entry.kind}`)}:{" "}
                    <strong>{p.display}</strong>
                    {p.entry.note ? ` – ${p.entry.note}` : ""}
                  </span>
                  <button
                    type="button"
                    className={iconBtn}
                    aria-label={t("circulation.remove", { name: p.display })}
                    onClick={() => setPlanned((list) => list.filter((_, i) => i !== index))}
                  >
                    <UiIcon icon={X} />
                  </button>
                </li>
              ))}
            </ol>
          )}
          <EntryFields draft={draft} onChange={setDraft} idPrefix="new-circulation-step" />
          <div>
            <button
              type="button"
              className={secondaryBtn}
              disabled={!draft.assignee.trim()}
              onClick={addPlanned}
            >
              {t("circulation.addToRoute")}
            </button>
          </div>
        </div>
      )}

      {error && (
        <p className="m-0 text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          className={primaryBtn}
          disabled={busy || !name.trim() || !definition || (needsRoute && planned.length === 0)}
        >
          {t("circulation.start")}
        </button>
        <button type="button" className={secondaryBtn} onClick={() => setOpen(false)}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}
