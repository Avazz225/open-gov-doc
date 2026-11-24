"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  downloadIncomingSignatureReport,
  getIncomingSignatures,
  revalidateIncomingSignatures,
  type DocumentSummary,
  type IncomingSignatures,
  type IncomingSignatureValidation,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { signerName, summaryForVersion } from "@/lib/incoming-signatures";
import { SignatureBadge } from "./SignatureBadge";

// "Eingegangene Signaturen" (P92-S3, ADR 0248 Decision 7): what
// signature-service found in the current version - one row per signature
// with signer, time, qualification, coverage and, for a failure, the reason
// in plain German; the ETSI report download and "erneut prüfen". Own
// signatures appear here too (validated against the internal CA).

const POLL_MS = 1500;
const POLL_TRIES = 20;

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = window.document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function IncomingSignaturesSection({ document: doc }: { document: DocumentSummary }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [data, setData] = useState<IncomingSignatures | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rechecking, setRechecking] = useState(false);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    if (!accessToken) return null;
    const result = await getIncomingSignatures(accessToken, doc.id);
    setData(result);
    return result;
  }, [accessToken, doc.id]);

  useEffect(() => {
    setData(null);
    setError(null);
    load().catch(() => setError(t("incomingSignatures.loadError")));
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, [load, t]);

  const version = doc.current_version_number;
  const summary = summaryForVersion(data?.summaries, version);
  const rows = (data?.validations ?? []).filter((v) => v.version_number === version);
  const runId = summary?.run_id ?? null;

  async function handleRecheck() {
    if (!accessToken) return;
    setError(null);
    setRechecking(true);
    try {
      await revalidateIncomingSignatures(accessToken, doc.id, version);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("incomingSignatures.recheckError"));
      setRechecking(false);
      return;
    }
    // The run happens in the background; poll until a new run shows up.
    let tries = 0;
    const poll = async () => {
      tries += 1;
      try {
        const result = await load();
        const newer = summaryForVersion(result?.summaries, version)?.run_id;
        if ((newer && newer !== runId) || tries >= POLL_TRIES) {
          setRechecking(false);
          return;
        }
      } catch {
        setRechecking(false);
        return;
      }
      pollTimer.current = setTimeout(poll, POLL_MS);
    };
    pollTimer.current = setTimeout(poll, POLL_MS);
  }

  async function handleReport(validation: IncomingSignatureValidation) {
    if (!accessToken) return;
    try {
      const blob = await downloadIncomingSignatureReport(accessToken, validation.run_id);
      saveBlob(blob, `pruefbericht-${doc.id}-v${validation.version_number}.xml`);
    } catch {
      setError(t("incomingSignatures.reportError"));
    }
  }

  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  const reportRow = rows.find((r) => r.report_available);

  return (
    <section className="incoming-signatures mt-4" aria-labelledby={`incoming-sig-${doc.id}`}>
      <h3 id={`incoming-sig-${doc.id}`} className="m-0 mb-2 text-sm font-semibold">
        {t("incomingSignatures.heading")}
      </h3>
      {data === null && !error ? (
        <p className="text-sm opacity-70">{t("common.loading")}</p>
      ) : !summary ? (
        <p className="text-sm italic opacity-70">{t("incomingSignatures.none")}</p>
      ) : (
        <>
          <p className="m-0 mb-2 flex flex-wrap items-center gap-2 text-sm">
            <SignatureBadge summary={summary} />
            <span className="opacity-80">
              {t("incomingSignatures.checkedAt", {
                date: new Date(summary.validated_at).toLocaleString(),
              })}
            </span>
          </p>
          {summary.truncated && (
            <p className="text-sm">{t("incomingSignatures.truncated")}</p>
          )}
          <ul className="incoming-signature-list m-0 list-none p-0">
            {rows.map((row, index) => (
              <IncomingSignatureRow key={`${row.run_id}-${index}`} row={row} />
            ))}
          </ul>
        </>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {reportRow && (
          <button type="button" className={secondaryBtn} onClick={() => handleReport(reportRow)}>
            {t("incomingSignatures.report")}
          </button>
        )}
        <button
          type="button"
          className={secondaryBtn}
          onClick={handleRecheck}
          disabled={rechecking}
        >
          {rechecking ? t("incomingSignatures.rechecking") : t("incomingSignatures.recheck")}
        </button>
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function IncomingSignatureRow({ row }: { row: IncomingSignatureValidation }) {
  const { t } = useI18n();
  const name = signerName(row) ?? t("incomingSignatures.unknownSigner");
  const proven = row.best_signature_time;
  const time = proven ?? row.claimed_signing_time;
  const tone =
    row.indication === "TOTAL-PASSED" ? "ok" : row.indication === "TOTAL-FAILED" ? "down" : "warn";
  return (
    <li className="incoming-signature-row border-b border-border py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`badge ${tone}`}>{t(`incomingSignatures.indication.${row.indication}`)}</span>
        <strong title={row.signer_subject ?? undefined}>
          {row.kind === "document_timestamp" ? t("incomingSignatures.timestampBy", { name }) : name}
        </strong>
        {row.kind !== "document_timestamp" && row.qualification !== "unknown" && (
          <span>{t(`incomingSignatures.qualification.${row.qualification}`)}</span>
        )}
      </div>
      {row.format === "cades" && row.position && (
        <p className="m-0 mt-1 text-xs opacity-80">
          {row.position.startsWith("enveloping")
            ? t("incomingSignatures.cadesEnveloping")
            : t("incomingSignatures.cadesDetached", { name: row.position.replace(/#\d+$/, "") })}
        </p>
      )}
      <dl className="incoming-signature-facts m-0 mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {time && (
          <>
            <dt className="opacity-70">
              {proven ? t("incomingSignatures.provenTime") : t("incomingSignatures.claimedTime")}
            </dt>
            <dd className="m-0">{new Date(time).toLocaleString()}</dd>
          </>
        )}
        {row.kind !== "file" && (
          <>
            <dt className="opacity-70">{t("incomingSignatures.coverageLabel")}</dt>
            <dd className="m-0">{t(`incomingSignatures.coverage.${row.coverage}`)}</dd>
          </>
        )}
        {row.trust_source && (
          <>
            <dt className="opacity-70">{t("incomingSignatures.trustLabel")}</dt>
            <dd className="m-0">{t(`incomingSignatures.trust.${row.trust_source}`)}</dd>
          </>
        )}
        {row.failure_reason && (
          <>
            <dt className="opacity-70">{t("incomingSignatures.reasonLabel")}</dt>
            <dd className="m-0">{row.failure_reason}</dd>
          </>
        )}
      </dl>
    </li>
  );
}
