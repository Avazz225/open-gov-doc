"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createLegalHold,
  getRetentionConfig,
  listLegalHolds,
  putDocumentRetention,
  releaseLegalHold,
  type DocumentSummary,
  type LegalHold,
  type RetentionConfig,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Sentinel select value for "reason not in the admin-curated catalog" -
// never sent to the server, only used to switch the UI to the free-text
// input (post-roadmap phase 31 session 1, ADR 0112).
const OTHER_REASON = "__other__";

function toDateInputValue(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) - attached
// using the same pattern as SignaturesPanel: its own list* call, its own
// load effect, below the metadata form. Whether the deletion reason is
// required/optional is enforced exclusively server-side (422 on
// violation), mirrored here only client-side via the error message.
export function RetentionPanel({ document: activeDocument }: { document: DocumentSummary }) {
  const { accessToken, user, permissions } = useAuth();
  const { t } = useI18n();
  // RBAC (post-roadmap phase 19 session 10, ADR 0075): setting/releasing a
  // legal hold requires `admin.legal_hold` server-side - the button stays
  // visible (the status display applies to every viewer), but is only
  // active for authorized principals. The server-side 403 remains the
  // actual enforcement; this is pure UX.
  const canManageLegalHold = permissions.includes("admin.legal_hold");
  const [retentionUntil, setRetentionUntil] = useState(
    toDateInputValue(activeDocument.retention_until)
  );
  const [fullDeletion, setFullDeletion] = useState(activeDocument.full_deletion);
  const [reason, setReason] = useState(activeDocument.pending_deletion_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [retentionConfig, setRetentionConfig] = useState<RetentionConfig | null>(null);
  const [reasonMode, setReasonMode] = useState<"catalog" | "other">("other");

  const [holds, setHolds] = useState<LegalHold[]>([]);
  const [holdReason, setHoldReason] = useState("");
  const [isHoldBusy, setIsHoldBusy] = useState(false);

  useEffect(() => {
    setRetentionUntil(toDateInputValue(activeDocument.retention_until));
    setFullDeletion(activeDocument.full_deletion);
    setReason(activeDocument.pending_deletion_reason ?? "");
    setError(null);
    setSaved(false);
  }, [activeDocument.id, activeDocument.retention_until, activeDocument.full_deletion, activeDocument.pending_deletion_reason]);

  useEffect(() => {
    if (!accessToken) return;
    listLegalHolds(accessToken, activeDocument.id, true)
      .then(setHolds)
      .catch(() => setHolds([]));
  }, [accessToken, activeDocument.id]);

  useEffect(() => {
    if (!accessToken) return;
    getRetentionConfig(accessToken)
      .then(setRetentionConfig)
      .catch(() => setRetentionConfig(null));
  }, [accessToken]);

  useEffect(() => {
    const currentReason = activeDocument.pending_deletion_reason ?? "";
    // Empty (no reason chosen yet) defaults to the dropdown, not the free-
    // text fallback - otherwise both would show at once on first load.
    setReasonMode(
      !currentReason || retentionConfig?.deletion_reason_catalog.includes(currentReason)
        ? "catalog"
        : "other"
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeDocument.id, retentionConfig]);

  const activeHold = holds.find((h) => h.released_at === null) ?? null;

  async function handleSubmit() {
    if (!accessToken) return;
    setError(null);
    setSaved(false);
    setIsSaving(true);
    try {
      await putDocumentRetention(accessToken, activeDocument.id, {
        retentionUntil: retentionUntil ? new Date(retentionUntil).toISOString() : null,
        fullDeletion,
        reason: reason.trim() || null,
      });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSetHold() {
    if (!accessToken || !user) return;
    setIsHoldBusy(true);
    setError(null);
    try {
      const hold = await createLegalHold(accessToken, {
        documentId: activeDocument.id,
        setBy: user.username,
        reason: holdReason.trim() || null,
      });
      setHolds((prev) => [hold, ...prev]);
      setHoldReason("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.holdError"));
    } finally {
      setIsHoldBusy(false);
    }
  }

  async function handleReleaseHold() {
    if (!accessToken || !user || !activeHold) return;
    setIsHoldBusy(true);
    setError(null);
    try {
      const released = await releaseLegalHold(accessToken, activeHold.id, user.username);
      setHolds((prev) => prev.map((h) => (h.id === released.id ? released : h)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("retention.holdError"));
    } finally {
      setIsHoldBusy(false);
    }
  }

  return (
    <section className="retention-panel" aria-label={t("retention.heading")}>
      <h2 className="pane-heading">{t("retention.heading")}</h2>

      {activeHold ? (
        <div className="legal-hold-active">
          <p className="hint">
            {t("retention.legalHoldActive", { setBy: activeHold.set_by })}
            {activeHold.reason ? ` (${activeHold.reason})` : ""}
          </p>
          <button
            type="button"
            onClick={handleReleaseHold}
            disabled={isHoldBusy || !canManageLegalHold}
            title={canManageLegalHold ? undefined : t("retention.legalHoldPermissionHint")}
          >
            {t("retention.releaseLegalHold")}
          </button>
        </div>
      ) : (
        <div className="legal-hold-form">
          <label>
            {t("retention.legalHoldReasonLabel")}
            <input
              value={holdReason}
              onChange={(e) => setHoldReason(e.target.value)}
              disabled={!canManageLegalHold}
            />
          </label>
          <button
            type="button"
            onClick={handleSetHold}
            disabled={isHoldBusy || !canManageLegalHold}
            title={canManageLegalHold ? undefined : t("retention.legalHoldPermissionHint")}
          >
            {t("retention.setLegalHold")}
          </button>
        </div>
      )}

      <label>
        {t("retention.retentionUntilLabel")}
        <input
          type="date"
          value={retentionUntil}
          onChange={(e) => setRetentionUntil(e.target.value)}
        />
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={fullDeletion}
          onChange={(e) => setFullDeletion(e.target.checked)}
        />
        {t("retention.fullDeletionLabel")}
      </label>
      {fullDeletion && retentionConfig && retentionConfig.deletion_reason_catalog.length > 0 && (
        <label>
          {t("retention.reasonLabel")}
          <select
            value={reasonMode === "catalog" ? reason : OTHER_REASON}
            onChange={(e) => {
              if (e.target.value === OTHER_REASON) {
                setReasonMode("other");
                setReason("");
              } else {
                setReasonMode("catalog");
                setReason(e.target.value);
              }
            }}
          >
            <option value="" disabled>
              {t("retention.reasonSelectPlaceholder")}
            </option>
            {retentionConfig.deletion_reason_catalog.map((entry) => (
              <option key={entry} value={entry}>
                {entry}
              </option>
            ))}
            <option value={OTHER_REASON}>{t("retention.reasonOther")}</option>
          </select>
        </label>
      )}
      {fullDeletion &&
        (reasonMode === "other" ||
          !retentionConfig ||
          retentionConfig.deletion_reason_catalog.length === 0) && (
          <label>
            {retentionConfig && retentionConfig.deletion_reason_catalog.length > 0
              ? t("retention.reasonOtherLabel")
              : t("retention.reasonLabel")}
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        )}
      <button type="button" onClick={handleSubmit} disabled={isSaving}>
        {isSaving ? t("retention.saving") : t("retention.save")}
      </button>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {saved && !error && <p className="hint">{t("retention.saved")}</p>}
    </section>
  );
}
