"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createRecordsQuarantine,
  listRecordsQuarantine,
  releaseRecordsQuarantine,
  type DocumentSummary,
  type RecordsQuarantine,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function toDateInputValue(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Records quarantine (14.2, post-roadmap phase 31 session 5, ADR 0116) - a
// fourth, independent lifecycle axis, structurally distinct from legal hold
// (RetentionPanel above): quarantine RESTRICTS visibility (the document
// disappears from folder listings while active) and can itself trigger
// destruction via an optional auto-delete date, whereas legal hold only
// PREVENTS deletion and never hides anything. Same set/release list-panel
// shape as RetentionPanel's legal-hold section, on its own `list*` call
// since quarantine state does not live on `DocumentSummary` itself.
//
// Reachable only while the document tab is already open (see ADR 0116
// "Consequences" - no cross-folder quarantine browser was built this
// session): `GET /documents/{id}` itself is unaffected by quarantine, only
// the folder listing excludes it, so an already-open tab keeps working for
// both quarantining and releasing.
export function RecordsQuarantinePanel({ document: activeDocument }: { document: DocumentSummary }) {
  const { accessToken, user, permissions } = useAuth();
  const { t } = useI18n();
  // RBAC (ADR 0116): a new, dedicated `admin.records_quarantine` capability,
  // deliberately not `admin.legal_hold` - opposing responsibilities (legal
  // hold protects records, quarantine schedules their destruction).
  const canManageQuarantine = permissions.includes("admin.records_quarantine");

  const [entries, setEntries] = useState<RecordsQuarantine[]>([]);
  const [reason, setReason] = useState("");
  const [autoDeleteAt, setAutoDeleteAt] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReason("");
    setAutoDeleteAt("");
    setError(null);
  }, [activeDocument.id]);

  useEffect(() => {
    if (!accessToken || !canManageQuarantine) {
      setEntries([]);
      return;
    }
    listRecordsQuarantine(accessToken, activeDocument.id, true)
      .then(setEntries)
      .catch(() => setEntries([]));
  }, [accessToken, activeDocument.id, canManageQuarantine]);

  const activeEntry = entries.find((e) => e.released_at === null) ?? null;

  // Ungated per ADR 0116: viewers without `admin.records_quarantine` don't
  // see quarantine state at all (unlike legal hold's always-visible status,
  // see RetentionPanel) - listing what's quarantined is itself the
  // restricted content this feature exists to protect.
  if (!canManageQuarantine) return null;

  async function handleSetQuarantine() {
    if (!accessToken || !user) return;
    setIsBusy(true);
    setError(null);
    try {
      const entry = await createRecordsQuarantine(accessToken, {
        documentId: activeDocument.id,
        setBy: user.username,
        reason: reason.trim() || null,
        autoDeleteAt: autoDeleteAt ? new Date(autoDeleteAt).toISOString() : null,
      });
      setEntries((prev) => [entry, ...prev]);
      setReason("");
      setAutoDeleteAt("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("recordsQuarantine.error"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleRelease() {
    if (!accessToken || !user || !activeEntry) return;
    setIsBusy(true);
    setError(null);
    try {
      const released = await releaseRecordsQuarantine(accessToken, activeEntry.id, user.username);
      setEntries((prev) => prev.map((e) => (e.id === released.id ? released : e)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("recordsQuarantine.error"));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <section className="records-quarantine-panel" aria-label={t("recordsQuarantine.heading")}>
      <h2 className="pane-heading">{t("recordsQuarantine.heading")}</h2>

      {activeEntry ? (
        <div className="quarantine-active">
          <p className="hint">
            {t("recordsQuarantine.active", { setBy: activeEntry.set_by })}
            {activeEntry.reason ? ` (${activeEntry.reason})` : ""}
            {activeEntry.auto_delete_at
              ? ` — ${t("recordsQuarantine.autoDeleteAt", {
                  date: toDateInputValue(activeEntry.auto_delete_at),
                })}`
              : ""}
          </p>
          <button type="button" onClick={handleRelease} disabled={isBusy}>
            {t("recordsQuarantine.release")}
          </button>
        </div>
      ) : (
        <div className="quarantine-form">
          <label>
            {t("recordsQuarantine.reasonLabel")}
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <label>
            {t("recordsQuarantine.autoDeleteAtLabel")}
            <input
              type="date"
              value={autoDeleteAt}
              onChange={(e) => setAutoDeleteAt(e.target.value)}
            />
          </label>
          <button type="button" onClick={handleSetQuarantine} disabled={isBusy}>
            {t("recordsQuarantine.set")}
          </button>
        </div>
      )}

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
