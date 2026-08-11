"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listQuarantinedScans,
  purgeQuarantinedScan,
  releaseQuarantinedScan,
  type ScanResult,
} from "@/lib/api";

// Quarantäne-Bereich (2.5/10.3, P15-S2) - Uploads, die den verpflichtenden
// Virenscan nicht bestanden haben und deshalb nie ein Dokument wurden (siehe
// virus-scan-service main.py). Zugriff selbst ist bereits serverseitig UND
// (siehe IconRail.tsx) client-seitig rollen-gegated - anders als die
// Papierkorb-Familie (P15-S1) gibt es hier keine "persönliche" Sicht.
// "Freigeben" muss `title` nachreichen (beim ursprünglich gescheiterten
// Upload wurde ja nie ein Dokument angelegt) - daher ein kleines Inline-
// Formular statt eines einzelnen Buttons wie bei "endgültig löschen".
export function QuarantinePane({ token }: { token: string }) {
  const { t } = useI18n();
  const [scans, setScans] = useState<ScanResult[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [releasingId, setReleasingId] = useState<string | null>(null);
  const [releaseTitle, setReleaseTitle] = useState("");
  const [releaseFolderId, setReleaseFolderId] = useState("");

  const reload = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      setScans(await listQuarantinedScans(token));
    } catch {
      setError(t("quarantine.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  function startRelease(scan: ScanResult) {
    setReleasingId(scan.id);
    setReleaseTitle(scan.filename);
    setReleaseFolderId("");
    setError(null);
  }

  async function confirmRelease(scan: ScanResult) {
    setBusyId(scan.id);
    setError(null);
    try {
      await releaseQuarantinedScan(token, scan.id, {
        title: releaseTitle,
        folderId: releaseFolderId.trim() || undefined,
      });
      setReleasingId(null);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("quarantine.releaseError"));
    } finally {
      setBusyId(null);
    }
  }

  async function handlePurge(scan: ScanResult) {
    if (!window.confirm(t("quarantine.purgeConfirm", { name: scan.filename }))) return;
    setBusyId(scan.id);
    setError(null);
    try {
      await purgeQuarantinedScan(token, scan.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("quarantine.purgeError"));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="quarantine-pane" aria-label={t("quarantine.paneLabel")}>
      <h2 className="pane-heading">{t("quarantine.heading")}</h2>
      <p className="hint">{t("quarantine.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : scans.length === 0 ? (
        <p className="empty-state">{t("quarantine.empty")}</p>
      ) : (
        <ul className="entry-list">
          {scans.map((scan) => (
            <li className="entry-row" key={scan.id}>
              <span className="entry-name">
                {scan.filename}
                {scan.threat_name ? ` — ${scan.threat_name}` : ""}
              </span>
              <span className="actions">
                <button
                  type="button"
                  onClick={() => startRelease(scan)}
                  disabled={busyId === scan.id}
                >
                  {t("quarantine.release")}
                </button>
                <button
                  type="button"
                  onClick={() => handlePurge(scan)}
                  disabled={busyId === scan.id}
                >
                  {t("quarantine.purge")}
                </button>
              </span>
              {releasingId === scan.id && (
                <div className="quarantine-release-form">
                  <label>
                    {t("quarantine.releaseTitleLabel")}
                    <input
                      type="text"
                      value={releaseTitle}
                      onChange={(e) => setReleaseTitle(e.target.value)}
                    />
                  </label>
                  <label>
                    {t("quarantine.releaseFolderLabel")}
                    <input
                      type="text"
                      value={releaseFolderId}
                      onChange={(e) => setReleaseFolderId(e.target.value)}
                      placeholder={t("quarantine.releaseFolderPlaceholder")}
                    />
                  </label>
                  <span className="actions">
                    <button
                      type="button"
                      onClick={() => confirmRelease(scan)}
                      disabled={busyId === scan.id || releaseTitle.trim() === ""}
                    >
                      {t("quarantine.releaseConfirm")}
                    </button>
                    <button type="button" onClick={() => setReleasingId(null)}>
                      {t("quarantine.releaseCancel")}
                    </button>
                  </span>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
