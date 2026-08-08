"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getLicenseStatus, uploadLicense, type LicenseStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function formatDimension(
  value: { limit: number | null; current: number | null; exceeded: boolean } | null
): string {
  if (value === null) return "-";
  const limit = value.limit === null ? "∞" : value.limit;
  return `${value.current ?? "-"} / ${limit}`;
}

// Lizenzstatus-Übersicht (Konzept 9.3, P9-S2): "jederzeit einsehbar, inkl.
// Restlaufzeit und Auslastung je Dimension" + Upload-Formular, da
// `POST /license` bislang nur per curl bedienbar war.
export function LicenseStatusView() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [licenseToken, setLicenseToken] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [uploadedAt, setUploadedAt] = useState<number | null>(null);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    try {
      setStatus(await getLicenseStatus(accessToken));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function handleUpload(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !licenseToken.trim()) return;
    setError(null);
    setUploadedAt(null);
    setIsUploading(true);
    try {
      const result = await uploadLicense(accessToken, licenseToken.trim());
      setStatus(result);
      setLicenseToken("");
      setUploadedAt(Date.now());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <div className="card">
      <p className="hint">{t("license.hint")}</p>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : status && !status.installed ? (
        <p className="empty-state">{t("license.notInstalled")}</p>
      ) : status ? (
        <table className="data-table" style={{ marginBottom: "1rem" }}>
          <tbody>
            <tr>
              <th>{t("license.status")}</th>
              <td>
                <span className={`badge ${status.valid ? "ok" : "down"}`}>
                  {status.valid ? t("license.valid") : t("license.invalid")}
                </span>
                {!status.valid && status.invalid_reason && ` (${status.invalid_reason})`}
              </td>
            </tr>
            <tr>
              <th>{t("license.expiresAt")}</th>
              <td>
                {status.expires_at ? new Date(status.expires_at).toLocaleString() : "-"}
                {status.days_remaining !== null &&
                  ` (${t("license.daysRemaining", { count: status.days_remaining })})`}
              </td>
            </tr>
            <tr>
              <th>{t("license.userModel")}</th>
              <td>{status.user_model ?? "-"}</td>
            </tr>
            <tr>
              <th>{t("license.users")}</th>
              <td>{formatDimension(status.users)}</td>
            </tr>
            <tr>
              <th>{t("license.storage")}</th>
              <td>{formatDimension(status.storage_gb)}</td>
            </tr>
            <tr>
              <th>{t("license.documents")}</th>
              <td>{formatDimension(status.documents)}</td>
            </tr>
            <tr>
              <th>{t("license.licensedComponents")}</th>
              <td>
                {status.licensed_components === null
                  ? t("license.allComponents")
                  : status.licensed_components.join(", ") || "-"}
              </td>
            </tr>
            {status.limits_exceeded.length > 0 && (
              <tr>
                <th>{t("license.limitsExceeded")}</th>
                <td>
                  <span className="badge down">{status.limits_exceeded.join(", ")}</span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      ) : null}

      <form onSubmit={handleUpload}>
        <label htmlFor="license-token">{t("license.uploadLabel")}</label>
        <textarea
          id="license-token"
          rows={4}
          style={{ width: "100%" }}
          value={licenseToken}
          onChange={(event) => setLicenseToken(event.target.value)}
          placeholder={t("license.uploadPlaceholder")}
        />
        <div className="actions">
          <button type="submit" disabled={isUploading || !licenseToken.trim()}>
            {isUploading ? t("common.loading") : t("license.uploadButton")}
          </button>
        </div>
      </form>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {uploadedAt !== null && !error && <p className="hint">{t("license.uploaded")}</p>}
    </div>
  );
}
