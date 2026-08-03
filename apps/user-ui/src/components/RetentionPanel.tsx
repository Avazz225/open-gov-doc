"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createLegalHold,
  listLegalHolds,
  putDocumentRetention,
  releaseLegalHold,
  type DocumentSummary,
  type LegalHold,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function toDateInputValue(iso: string | null): string {
  if (!iso) return "";
  return iso.slice(0, 10);
}

// Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - Anbau-
// Muster wie SignaturesPanel: eigener list*-Aufruf, eigener Lade-Effekt,
// unterhalb des Metadaten-Formulars. Pflicht/optional für den Löschgrund
// wird ausschließlich serverseitig durchgesetzt (422 bei Verstoß), hier nur
// clientseitig gespiegelt über die Fehlermeldung.
export function RetentionPanel({ document: activeDocument }: { document: DocumentSummary }) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const [retentionUntil, setRetentionUntil] = useState(
    toDateInputValue(activeDocument.retention_until)
  );
  const [fullDeletion, setFullDeletion] = useState(activeDocument.full_deletion);
  const [reason, setReason] = useState(activeDocument.pending_deletion_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

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
          <button type="button" onClick={handleReleaseHold} disabled={isHoldBusy}>
            {t("retention.releaseLegalHold")}
          </button>
        </div>
      ) : (
        <div className="legal-hold-form">
          <label>
            {t("retention.legalHoldReasonLabel")}
            <input value={holdReason} onChange={(e) => setHoldReason(e.target.value)} />
          </label>
          <button type="button" onClick={handleSetHold} disabled={isHoldBusy}>
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
      {fullDeletion && (
        <label>
          {t("retention.reasonLabel")}
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
