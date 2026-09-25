"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getDocument,
  listRecordsQuarantine,
  releaseRecordsQuarantine,
  type DocumentSummary,
  type RecordsQuarantine,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

interface Row {
  entry: RecordsQuarantine;
  title: string | null;
}

// Installation-wide records-quarantine browsing view (14.2, Post-Roadmap
// Phase 36 Session 2, ADR 0116) - the cross-folder browsing UI ADR 0116's
// own "Consequences" named as a reasonable follow-up ("analogous to
// TrashPane's admin/admin_classified scopes"). Unlike TrashPane, no new
// backend endpoint was needed: `GET /records-quarantine?active_only=true`
// (without `document_id`) already lists every active quarantine
// installation-wide - it was simply never called without a `document_id`
// filter from this app before. Read + release only (mirrors TrashPane's
// "list already-existing entries, act on them" shape) - STARTING a new
// quarantine stays `RecordsQuarantinePanel.tsx`'s per-document concern,
// since curating a reason/auto-delete date is most naturally done while
// already looking at the specific document.
export function RecordsQuarantineOverviewPane({
  token,
  onOpenDocument,
}: {
  token: string;
  onOpenDocument: (doc: DocumentSummary) => void;
}) {
  const { t } = useI18n();
  const { user } = useAuth();

  const [rows, setRows] = useState<Row[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const entries = await listRecordsQuarantine(token, undefined, true);
      const resolved = await Promise.all(
        entries.map(async (entry) => {
          try {
            const doc = await getDocument(token, entry.document_id);
            return { entry, title: doc.title };
          } catch {
            return { entry, title: null };
          }
        })
      );
      setRows(resolved);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleOpen(row: Row) {
    try {
      onOpenDocument(await getDocument(token, row.entry.document_id));
    } catch {
      // Deleted/not readable since quarantine was set - nothing to open.
    }
  }

  async function handleRelease(row: Row) {
    if (!user) return;
    setBusyId(row.entry.id);
    setError(null);
    try {
      await releaseRecordsQuarantine(token, row.entry.id, user.username);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("recordsQuarantineOverview.releaseError"));
    } finally {
      setBusyId(null);
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <section
      className="records-quarantine-overview-pane"
      aria-label={t("recordsQuarantineOverview.paneLabel")}
    >
      <h2 className="m-0 mb-3 text-base">{t("recordsQuarantineOverview.heading")}</h2>
      <p className="text-sm opacity-80">{t("recordsQuarantineOverview.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : rows.length === 0 ? (
        <p className="italic opacity-70">{t("recordsQuarantineOverview.empty")}</p>
      ) : (
        <ul className="entry-list">
          {rows.map((row) => (
            <li className="entry-row" key={row.entry.id}>
              {row.title === null ? (
                <span className="entry-name">{t("recordsQuarantineOverview.documentGone")}</span>
              ) : (
                <button type="button" className="entry-name" onClick={() => handleOpen(row)}>
                  {row.title}
                </button>
              )}
              <span className="entry-meta">
                {t("recordsQuarantineOverview.setBy", { setBy: row.entry.set_by })}
                {row.entry.reason ? ` — ${row.entry.reason}` : ""}
                {row.entry.auto_delete_at
                  ? ` — ${t("recordsQuarantineOverview.autoDeleteAt", {
                      date: new Date(row.entry.auto_delete_at).toLocaleDateString(),
                    })}`
                  : ""}
              </span>
              <span className="flex gap-2">
                <button
                  type="button"
                  className={secondaryBtn}
                  onClick={() => handleRelease(row)}
                  disabled={busyId === row.entry.id}
                >
                  {t("recordsQuarantineOverview.release")}
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
