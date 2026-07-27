"use client";

import { useI18n } from "@/i18n";
import { type DocumentSummary, downloadDocument } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Rechte Spalte des 3-Spalten-Layouts (Nutzer-Feedback nach P4-S3, 8) -
// vom Tab-gesteuerten aktiven Dokument synchronisiert. Zeigt weiterhin
// bewusst nur einen Stub statt echtem Rendering, da der Rendering/Preview
// Service noch nicht existiert (folgt in P5-S2) - anders als der modale
// Vorgänger (`PreviewStub`, P4-S2) ist dies jetzt ein permanenter, ins
// Layout eingebetteter Bereich statt eines Overlays.
export function PreviewPane({ document: activeDocument }: { document: DocumentSummary | null }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  async function handleDownload() {
    if (!accessToken || !activeDocument) return;
    try {
      const blob = await downloadDocument(accessToken, activeDocument.id);
      triggerBrowserDownload(blob, activeDocument.title);
    } catch {
      // Download-Fehler an dieser Stelle bewusst nicht separat behandelt -
      // die Explorer-Spalte zeigt bereits einen globalen Fehlerbereich.
    }
  }

  if (!activeDocument) {
    return (
      <section className="preview-pane" aria-label={t("preview.paneLabel")}>
        <p className="empty-state">{t("preview.noSelection")}</p>
      </section>
    );
  }

  return (
    <section className="preview-pane" aria-label={t("preview.paneLabel")}>
      <h2 className="pane-heading">{activeDocument.title}</h2>
      <p>{t("preview.stubMessage")}</p>
      <button type="button" onClick={handleDownload}>
        {t("folderBrowser.download")}
      </button>
    </section>
  );
}
