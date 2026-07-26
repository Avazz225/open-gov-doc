"use client";

import { useI18n } from "@/i18n";

// Platzhalter für den eigentlichen Rendering/Preview Service (3.7/2.4,
// P5-S2) - der existiert noch nicht. Zeigt bewusst deutlich an, dass hier
// noch keine echte Vorschau steckt, statt eine leere/kaputte Ansicht zu
// zeigen.
export function PreviewStub({
  title,
  onClose,
}: {
  title: string;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <h2>{t("previewStub.heading", { title })}</h2>
        <p>{t("previewStub.message")}</p>
        <button type="button" onClick={onClose}>
          {t("previewStub.close")}
        </button>
      </div>
    </div>
  );
}
