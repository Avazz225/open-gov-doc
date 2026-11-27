"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DeadLetter,
  type DeadLetterDetail,
  discardDeadLetter,
  getDeadLetter,
  listDeadLetters,
  replayDeadLetter,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Dead letters (P99-S2, ADR 0270): events a service gave up on after every
// delivery attempt. The service went on with the rest; these wait here.
// Replay hands one to exactly the consumer that failed (after the cause is
// fixed); discard drops it with a reason. Both are audited. The list itself
// needs `admin.monitoring`, since payloads can hold personal data.

const card = "rounded-lg border border-border p-4 mb-6";
const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent-bg disabled:opacity-60";
const primaryButton =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const plainButton =
  "rounded-md border border-border bg-transparent px-2 py-1 text-sm text-fg hover:bg-hover-bg disabled:opacity-50 whitespace-nowrap";

type Action = "replay" | "discard";

export function DeadLetters({ canManage }: { canManage: boolean }) {
  const { accessToken } = useAuth();
  const { t, locale } = useI18n();
  const [letters, setLetters] = useState<DeadLetter[] | null>(null);
  const [open, setOpen] = useState<DeadLetterDetail | null>(null);
  const [action, setAction] = useState<{ seq: number; kind: Action } | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    if (!accessToken || !canManage) return;
    try {
      setLetters(await listDeadLetters(accessToken));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, canManage, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function show(seq: number) {
    if (!accessToken) return;
    if (open?.seq === seq) {
      setOpen(null);
      return;
    }
    try {
      setOpen(await getDeadLetter(accessToken, seq));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function confirm(seq: number, kind: Action) {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      if (kind === "replay") {
        const letter = await replayDeadLetter(accessToken, seq, reason.trim());
        setStatus(t("deadLetters.replayed", { consumer: letter.consumer }));
      } else {
        await discardDeadLetter(accessToken, seq, reason.trim());
        setStatus(t("deadLetters.discarded"));
      }
      setAction(null);
      setReason("");
      if (open?.seq === seq) setOpen(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.actionError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={card} aria-labelledby="dead-letters-heading">
      <h2 id="dead-letters-heading">{t("deadLetters.heading")}</h2>
      <p className="text-sm opacity-80">{t("deadLetters.hint")}</p>
      {!canManage && <p className="text-sm italic opacity-80">{t("deadLetters.needsRole")}</p>}
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {status && (
        <p role="status" className="text-sm">
          {status}
        </p>
      )}
      {canManage && letters !== null && letters.length === 0 && (
        <p className="text-sm">{t("deadLetters.none")}</p>
      )}
      {canManage && letters !== null && letters.length > 0 && (
        <ul className="m-0 list-none p-0">
          {letters.map((letter) => (
            <li key={letter.seq} className="border-t border-border py-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <p className="m-0 font-semibold break-all">
                    {letter.consumer} · <code>{letter.subject}</code>
                  </p>
                  <p className="m-0 text-sm break-all text-danger">{letter.error}</p>
                  <p className="m-0 text-xs opacity-70">
                    {t("deadLetters.meta", {
                      when: letter.failed_at
                        ? new Date(letter.failed_at).toLocaleString(locale)
                        : "?",
                      deliveries: letter.deliveries,
                    })}
                    {letter.replayed_from ? ` · ${t("deadLetters.again")}` : ""}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className={plainButton}
                    aria-expanded={open?.seq === letter.seq}
                    onClick={() => void show(letter.seq)}
                  >
                    {t("deadLetters.show")}
                  </button>
                  <button
                    type="button"
                    className={plainButton}
                    onClick={() => {
                      setAction({ seq: letter.seq, kind: "replay" });
                      setReason("");
                    }}
                  >
                    {t("deadLetters.replay")}
                  </button>
                  <button
                    type="button"
                    className={plainButton}
                    onClick={() => {
                      setAction({ seq: letter.seq, kind: "discard" });
                      setReason("");
                    }}
                  >
                    {t("deadLetters.discard")}
                  </button>
                </div>
              </div>
              {open?.seq === letter.seq && (
                <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-hover-bg p-2 text-xs whitespace-pre-wrap break-all">
                  {open.payload}
                  {open.payload_truncated ? "\n…" : ""}
                </pre>
              )}
              {action?.seq === letter.seq && (
                <form
                  className="mt-2 flex flex-wrap items-end gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void confirm(letter.seq, action.kind);
                  }}
                >
                  <label className="flex min-w-0 flex-col gap-1 text-sm">
                    {action.kind === "replay"
                      ? t("deadLetters.replayReason")
                      : t("deadLetters.discardReason")}
                    <input
                      className={`${fieldInput} w-80 max-w-full`}
                      value={reason}
                      minLength={3}
                      required
                      onChange={(e) => setReason(e.target.value)}
                    />
                  </label>
                  <button
                    type="submit"
                    className={primaryButton}
                    disabled={busy || reason.trim().length < 3}
                  >
                    {action.kind === "replay"
                      ? t("deadLetters.replayConfirm")
                      : t("deadLetters.discardConfirm")}
                  </button>
                  <button type="button" className={plainButton} onClick={() => setAction(null)}>
                    {t("deadLetters.cancel")}
                  </button>
                </form>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
