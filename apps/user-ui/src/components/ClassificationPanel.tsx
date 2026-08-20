"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, setDocumentClassificationLevel, type DocumentSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { raisableClassificationLevels, type ClassificationLevel } from "@/lib/classification";

// Classification level (14.2, post-roadmap phase 31 session 3, ADR 0114) -
// attached below the metadata form using the same standalone-panel pattern
// as RetentionPanel/SignaturesPanel: its own load-independent state, synced
// to the active document via effect (MetadataPanel is not remounted per
// document, only its `document` prop changes).
export function ClassificationPanel({
  document: activeDocument,
  onChanged,
}: {
  document: DocumentSummary;
  onChanged: (updated: DocumentSummary) => void;
}) {
  const { accessToken, user, permissions } = useAuth();
  const { t } = useI18n();
  // RBAC (ADR 0114): raising the level requires `admin.classification`
  // server-side - the current level stays visible to every viewer, only the
  // raise control is gated. Same UX pattern as RetentionPanel's
  // `canManageLegalHold`.
  const canClassify = permissions.includes("admin.classification");
  const raisable = raisableClassificationLevels(activeDocument.classification_level);
  const [selected, setSelected] = useState(raisable[0]);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    setSelected(raisableClassificationLevels(activeDocument.classification_level)[0]);
  }, [activeDocument.id, activeDocument.classification_level]);

  async function handleRaise() {
    if (!accessToken) return;
    setError(null);
    setIsSaving(true);
    try {
      const updated = await setDocumentClassificationLevel(
        accessToken,
        activeDocument.id,
        selected,
        user?.username ?? ""
      );
      onChanged(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("classification.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  // A "no-op raise" (same level picked again) is disabled - but going from
  // unclassified (`null`) to the first named level is a real action, so this
  // deliberately does NOT fall back to `raisable[0]` the way the effect
  // above does for the select's initial value.
  const isNoOpRaise = selected === activeDocument.classification_level;

  return (
    <section className="classification-panel" aria-label={t("classification.paneLabel")}>
      <h2 className="pane-heading">{t("classification.heading")}</h2>
      <p>
        {activeDocument.classification_level ?? t("classification.unclassified")}
      </p>

      {canClassify && (
        <div className="classification-raise">
          <label>
            {t("classification.raiseToLabel")}
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value as ClassificationLevel)}
            >
              {raisable.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </label>
          <button type="button" onClick={handleRaise} disabled={isSaving || isNoOpRaise}>
            {isSaving ? t("classification.saving") : t("classification.raiseAction")}
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
