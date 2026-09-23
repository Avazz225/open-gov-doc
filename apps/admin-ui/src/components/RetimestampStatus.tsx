"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listSignaturesDueForRetimestamp,
  type SignatureDueForRetimestamp,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// PAdES-B-LTA archive-timestamp-chain re-timestamping poll loop (3.10,
// ADR 0155) - admin visibility only (P71-S4). Deliberately read-only: ADR
// 0155 itself already decided against a manual trigger, citing document-
// service's own retention poll loop as the established "no manual
// trigger" precedent - this session found no new justification to
// reopen that decision, so no retry/trigger button exists here.
export function RetimestampStatus() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [signatures, setSignatures] = useState<SignatureDueForRetimestamp[]>([]);
  const [unreachable, setUnreachable] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!accessToken) return;
    setIsLoading(true);
    setUnreachable(false);
    setError(null);
    try {
      setSignatures(await listSignaturesDueForRetimestamp(accessToken));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setUnreachable(true);
      }
    } finally {
      setIsLoading(false);
    }
  }, [accessToken]);

  useEffect(() => {
    reload();
  }, [reload]);

  return (
    <div className="card">
      <h2>{t("retimestampStatus.heading")}</h2>
      <p className="text-sm opacity-80">{t("retimestampStatus.hint")}</p>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : unreachable ? (
        <p className="italic opacity-70">{t("retimestampStatus.unreachable")}</p>
      ) : signatures.length === 0 ? (
        <p className="italic opacity-70">{t("retimestampStatus.empty")}</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>{t("retimestampStatus.documentColumn")}</th>
              <th>{t("retimestampStatus.levelColumn")}</th>
              <th>{t("retimestampStatus.connectorColumn")}</th>
              <th>{t("retimestampStatus.signerColumn")}</th>
              <th>{t("retimestampStatus.signedAtColumn")}</th>
              <th>{t("retimestampStatus.lastTimestampedColumn")}</th>
            </tr>
          </thead>
          <tbody>
            {signatures.map((signature) => (
              <tr key={signature.id}>
                <td>{signature.document_id}</td>
                <td>{signature.level}</td>
                <td>{signature.connector_id}</td>
                <td>{signature.signer_display_name}</td>
                <td>{new Date(signature.signed_at).toLocaleString()}</td>
                <td>
                  {signature.last_timestamped_at
                    ? new Date(signature.last_timestamped_at).toLocaleString()
                    : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
