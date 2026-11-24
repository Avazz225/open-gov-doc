"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  addIncomingTrustAnchor,
  deleteIncomingTrustAnchor,
  getBackfillStatus,
  getIncomingValidationConfig,
  getTrustedLists,
  importTrustedList,
  listIncomingTrustAnchors,
  putIncomingValidationConfig,
  refreshTrustedLists,
  startBackfill,
  type BackfillStatus,
  type IncomingTrustAnchor,
  type IncomingValidationConfig,
  type TrustedListEntry,
  type TrustedListsStatus,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { BoundedList, useListFilter } from "./BoundedList";

// Validating incoming signatures (P92-S3, ADR 0248 Decision 7): the
// switches, the EU trusted lists, the installation's own trust anchors and
// the backfill of existing documents - four sections on the signature page.

const POLL_MS = 2000;

const primaryBtn =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const secondaryBtn =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
const checkbox = "h-4 w-4 rounded border-border accent-accent";
const section = "rounded-lg border border-border p-4 mb-6";

function fmt(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "–";
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function IncomingSignatureSettings() {
  return (
    <>
      <ValidationSwitches />
      <TrustedListsSection />
      <TrustAnchorsSection />
      <BackfillSection />
    </>
  );
}

function ValidationSwitches() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<IncomingValidationConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    getIncomingValidationConfig(accessToken)
      .then(setConfig)
      .catch((err) => setError(errorText(err, t("incomingSignatures.loadError"))));
  }, [accessToken, t]);

  async function save(next: IncomingValidationConfig) {
    if (!accessToken) return;
    setError(null);
    setSaved(false);
    try {
      setConfig(
        await putIncomingValidationConfig(accessToken, {
          enabled: next.enabled,
          revocation_mode: next.revocation_mode,
          outbound_fetching: next.outbound_fetching,
          eu_trusted_lists: next.eu_trusted_lists,
        })
      );
      setSaved(true);
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.saveError")));
    }
  }

  return (
    <section className={section} aria-labelledby="incoming-switches-heading">
      <h2 id="incoming-switches-heading" className="m-0 mb-2 text-base">
        {t("incomingSignatures.switchesHeading")}
      </h2>
      <p className="text-sm opacity-80">{t("incomingSignatures.switchesHint")}</p>
      {config && (
        <div className="flex flex-col gap-3">
          <label className="checkbox-label">
            <input
              type="checkbox"
              className={checkbox}
              checked={config.enabled}
              onChange={(e) => save({ ...config, enabled: e.target.checked })}
            />
            {t("incomingSignatures.enabled")}
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              className={checkbox}
              checked={config.eu_trusted_lists}
              onChange={(e) => save({ ...config, eu_trusted_lists: e.target.checked })}
            />
            {t("incomingSignatures.euTrustedLists")}
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              className={checkbox}
              checked={config.outbound_fetching}
              onChange={(e) => save({ ...config, outbound_fetching: e.target.checked })}
            />
            {t("incomingSignatures.outboundFetching")}
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("incomingSignatures.revocationMode")}
            <select
              className={`${fieldInput} max-w-md`}
              value={config.revocation_mode}
              onChange={(e) =>
                save({
                  ...config,
                  revocation_mode: e.target.value as IncomingValidationConfig["revocation_mode"],
                })
              }
            >
              <option value="soft-fail">{t("incomingSignatures.softFail")}</option>
              <option value="hard-fail">{t("incomingSignatures.hardFail")}</option>
            </select>
          </label>
          <p className="m-0 text-xs opacity-70">
            {t("incomingSignatures.updatedBy", {
              date: fmt(config.updated_at),
              who: config.updated_by ?? "–",
            })}
          </p>
        </div>
      )}
      {saved && (
        <p role="status" className="text-sm">
          {t("incomingSignatures.saved")}
        </p>
      )}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

const listText = (entry: TrustedListEntry) => `${entry.territory} ${entry.location_uri ?? ""}`;

function TrustedListsSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [status, setStatus] = useState<TrustedListsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { query, setQuery, filtered } = useListFilter(status?.lists ?? [], listText);

  const load = useCallback(async () => {
    if (!accessToken) return null;
    const next = await getTrustedLists(accessToken);
    setStatus(next);
    return next;
  }, [accessToken]);

  useEffect(() => {
    load().catch((err) => setError(errorText(err, t("incomingSignatures.loadError"))));
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load, t]);

  async function handleRefresh() {
    if (!accessToken) return;
    setError(null);
    const before = status?.last_fetch?.started_at ?? null;
    setFetching(true);
    try {
      await refreshTrustedLists(accessToken);
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.refreshError")));
      setFetching(false);
      return;
    }
    const poll = async () => {
      try {
        const next = await load();
        const fetch = next?.last_fetch;
        if (fetch && fetch.started_at !== before && fetch.finished_at) {
          setFetching(false);
          return;
        }
      } catch {
        setFetching(false);
        return;
      }
      timer.current = setTimeout(poll, POLL_MS);
    };
    timer.current = setTimeout(poll, POLL_MS);
  }

  async function handleImport(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !accessToken) return;
    setError(null);
    setImportMessage(null);
    try {
      const entry = await importTrustedList(accessToken, file);
      setImportMessage(
        t("incomingSignatures.imported", {
          name: entry.kind === "lotl" ? t("incomingSignatures.lotl") : entry.territory,
        })
      );
      await load();
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.importError")));
    }
  }

  const last = status?.last_fetch;
  return (
    <section className={section} aria-labelledby="trusted-lists-heading">
      <h2 id="trusted-lists-heading" className="m-0 mb-2 text-base">
        {t("incomingSignatures.trustedListsHeading")}
      </h2>
      <p className="text-sm opacity-80">{t("incomingSignatures.trustedListsHint")}</p>
      {status && (
        <dl className="m-0 mb-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          <dt className="opacity-70">{t("incomingSignatures.lastFetch")}</dt>
          <dd className="m-0">
            {last
              ? t("incomingSignatures.lastFetchValue", {
                  date: fmt(last.finished_at ?? last.started_at),
                  count: last.lists_stored,
                  source:
                    last.source === "import"
                      ? t("incomingSignatures.sourceImport")
                      : t("incomingSignatures.sourceFetch"),
                })
              : t("incomingSignatures.neverFetched")}
          </dd>
          <dt className="opacity-70">{t("incomingSignatures.schedule")}</dt>
          <dd className="m-0">
            {status.poll_interval_seconds > 0
              ? t("incomingSignatures.scheduleEvery", {
                  hours: Math.round(status.poll_interval_seconds / 3600),
                })
              : t("incomingSignatures.scheduleOff")}
          </dd>
        </dl>
      )}
      {last && last.errors.length > 0 && (
        <details className="mb-3 text-sm">
          <summary>{t("incomingSignatures.fetchErrors", { count: last.errors.length })}</summary>
          <ul className="m-0 mt-1 pl-5">
            {last.errors.map((message) => (
              <li key={message} className="break-words">
                {message}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="mb-3 flex flex-wrap gap-2">
        <button type="button" className={primaryBtn} onClick={handleRefresh} disabled={fetching}>
          {fetching ? t("incomingSignatures.fetching") : t("incomingSignatures.fetchNow")}
        </button>
        <button type="button" className={secondaryBtn} onClick={() => fileInput.current?.click()}>
          {t("incomingSignatures.importList")}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".xml,application/xml,text/xml"
          className="hidden"
          aria-label={t("incomingSignatures.importList")}
          onChange={handleImport}
        />
      </div>
      {importMessage && (
        <p role="status" className="text-sm">
          {importMessage}
        </p>
      )}
      {status && status.lists.length === 0 ? (
        <p className="italic opacity-70">{t("incomingSignatures.noLists")}</p>
      ) : status ? (
        <BoundedList
          label={t("incomingSignatures.trustedListsHeading")}
          total={status.lists.length}
          shown={filtered.length}
          query={query}
          onQueryChange={setQuery}
        >
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("incomingSignatures.territory")}</th>
                <th>{t("incomingSignatures.sequence")}</th>
                <th>{t("incomingSignatures.issued")}</th>
                <th>{t("incomingSignatures.nextUpdate")}</th>
                <th>{t("incomingSignatures.source")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((entry) => (
                <tr key={`${entry.kind}-${entry.territory}`}>
                  <td title={entry.location_uri ?? undefined}>
                    {entry.kind === "lotl" ? t("incomingSignatures.lotl") : entry.territory}
                  </td>
                  <td>{entry.sequence_number ?? "–"}</td>
                  <td>{fmt(entry.issued_at)}</td>
                  <td>{fmt(entry.next_update)}</td>
                  <td>
                    {entry.source === "import"
                      ? t("incomingSignatures.sourceImport")
                      : t("incomingSignatures.sourceFetch")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </BoundedList>
      ) : null}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function TrustAnchorsSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [anchors, setAnchors] = useState<IncomingTrustAnchor[]>([]);
  const [label, setLabel] = useState("");
  const [pem, setPem] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!accessToken) return;
    setAnchors(await listIncomingTrustAnchors(accessToken));
  }, [accessToken]);

  useEffect(() => {
    load().catch((err) => setError(errorText(err, t("incomingSignatures.loadError"))));
  }, [load, t]);

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    setBusy(true);
    try {
      await addIncomingTrustAnchor(accessToken, label.trim(), pem.trim());
      setLabel("");
      setPem("");
      await load();
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.saveError")));
    } finally {
      setBusy(false);
    }
  }

  async function handleFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) setPem(await file.text());
  }

  async function handleDelete(anchor: IncomingTrustAnchor) {
    if (!accessToken || anchor.id === null) return;
    if (!window.confirm(t("incomingSignatures.removeConfirm", { name: anchor.label }))) return;
    setError(null);
    try {
      await deleteIncomingTrustAnchor(accessToken, anchor.id);
      await load();
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.saveError")));
    }
  }

  return (
    <section className={section} aria-labelledby="trust-anchors-heading">
      <h2 id="trust-anchors-heading" className="m-0 mb-2 text-base">
        {t("incomingSignatures.anchorsHeading")}
      </h2>
      <p className="text-sm opacity-80">{t("incomingSignatures.anchorsHint")}</p>
      <div className="dms-scroll-x">
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("incomingSignatures.anchorLabel")}</th>
              <th>{t("incomingSignatures.anchorSubject")}</th>
              <th>{t("incomingSignatures.anchorValidUntil")}</th>
              <th>
                <span className="sr-only">{t("incomingSignatures.actions")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {anchors.map((anchor) => (
              <tr key={anchor.id ?? "internal"}>
                <td>
                  {anchor.label}
                  {anchor.source === "internal" && (
                    <span className="badge neutral ml-2">{t("incomingSignatures.internal")}</span>
                  )}
                </td>
                <td className="break-all" title={anchor.fingerprint_sha256}>
                  {anchor.subject}
                </td>
                <td>{new Date(anchor.not_after).toLocaleDateString()}</td>
                <td>
                  {anchor.id !== null && (
                    <button
                      type="button"
                      className={secondaryBtn}
                      onClick={() => handleDelete(anchor)}
                      aria-label={t("incomingSignatures.removeAnchor", { name: anchor.label })}
                    >
                      {t("incomingSignatures.remove")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form
        className="mt-3 flex flex-col gap-2"
        onSubmit={handleAdd}
        aria-label={t("incomingSignatures.addAnchor")}
      >
        <label className="flex flex-col gap-1 text-sm">
          {t("incomingSignatures.anchorLabel")}
          <input
            className={`${fieldInput} max-w-md`}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            required
            maxLength={256}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("incomingSignatures.anchorPem")}
          <textarea
            className={`${fieldInput} min-h-28 font-mono text-xs`}
            value={pem}
            onChange={(e) => setPem(e.target.value)}
            placeholder="-----BEGIN CERTIFICATE-----"
            required
          />
        </label>
        <label className="text-sm">
          {t("incomingSignatures.anchorFile")}{" "}
          <input type="file" accept=".pem,.crt,.cer" onChange={handleFile} />
        </label>
        <div>
          <button type="submit" className={primaryBtn} disabled={busy}>
            {t("incomingSignatures.addAnchor")}
          </button>
        </div>
      </form>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function BackfillSection() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [folderId, setFolderId] = useState("");
  const [createdFrom, setCreatedFrom] = useState("");
  const [createdTo, setCreatedTo] = useState("");
  const [onlyUnvalidated, setOnlyUnvalidated] = useState(true);
  const [status, setStatus] = useState<BackfillStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const poll = useCallback(async () => {
    if (!accessToken) return;
    try {
      const next = await getBackfillStatus(accessToken);
      setStatus(next);
      if (next.state === "running") timer.current = setTimeout(poll, POLL_MS);
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.loadError")));
    }
  }, [accessToken, t]);

  useEffect(() => {
    poll();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  async function handleStart(event: React.FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    try {
      setStatus(
        await startBackfill(accessToken, {
          folderId: folderId.trim() || undefined,
          // Dates as local days: from the start of the first to the end of
          // the last.
          createdFrom: createdFrom ? new Date(`${createdFrom}T00:00:00`).toISOString() : undefined,
          createdTo: createdTo ? new Date(`${createdTo}T23:59:59`).toISOString() : undefined,
          onlyUnvalidated,
        })
      );
      timer.current = setTimeout(poll, POLL_MS);
    } catch (err) {
      setError(errorText(err, t("incomingSignatures.backfillError")));
    }
  }

  const running = status?.state === "running";
  const noCriteria = !folderId.trim() && !createdFrom && !createdTo;
  return (
    <section className={section} aria-labelledby="backfill-heading">
      <h2 id="backfill-heading" className="m-0 mb-2 text-base">
        {t("incomingSignatures.backfillHeading")}
      </h2>
      <p className="text-sm opacity-80">{t("incomingSignatures.backfillHint")}</p>
      <form className="flex flex-col gap-2" onSubmit={handleStart} aria-label={t("incomingSignatures.backfillHeading")}>
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">
            {t("incomingSignatures.backfillFrom")}
            <input
              type="date"
              className={fieldInput}
              value={createdFrom}
              onChange={(e) => setCreatedFrom(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("incomingSignatures.backfillTo")}
            <input
              type="date"
              className={fieldInput}
              value={createdTo}
              onChange={(e) => setCreatedTo(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            {t("incomingSignatures.backfillFolder")}
            <input
              className={fieldInput}
              value={folderId}
              onChange={(e) => setFolderId(e.target.value)}
              placeholder={t("incomingSignatures.backfillFolderPlaceholder")}
            />
          </label>
        </div>
        <label className="checkbox-label text-sm">
          <input
            type="checkbox"
            className={checkbox}
            checked={onlyUnvalidated}
            onChange={(e) => setOnlyUnvalidated(e.target.checked)}
          />
          {t("incomingSignatures.backfillOnlyUnvalidated")}
        </label>
        <div>
          <button type="submit" className={primaryBtn} disabled={running || noCriteria}>
            {running ? t("incomingSignatures.backfillRunning") : t("incomingSignatures.backfillStart")}
          </button>
        </div>
      </form>
      {status && status.state !== "idle" && (
        <p role="status" className="mt-3 text-sm" data-testid="backfill-status">
          {t(`incomingSignatures.backfillState.${status.state}`, {
            scanned: status.scanned,
            queued: status.queued,
            skipped: status.skipped_validated,
            pending: status.queue_pending ?? 0,
          })}
          {status.limit_reached && ` ${t("incomingSignatures.backfillLimit")}`}
          {status.error && ` (${status.error})`}
        </p>
      )}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
