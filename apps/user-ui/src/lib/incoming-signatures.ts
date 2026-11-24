"use client";

import { useEffect, useState } from "react";
import {
  getIncomingSignatureSummaries,
  type IncomingSignatureSummary,
  type IncomingSignatureValidation,
} from "@/lib/api";

// What a document list shows for signed PDFs (P92-S3, ADR 0248 Decision 7):
// one badge per document, from the summary of its current version.

export type SignatureBadgeKind = "qualified" | "valid" | "invalid" | "unchecked";

export function badgeKind(summary: IncomingSignatureSummary): SignatureBadgeKind {
  if (summary.worst_indication === "TOTAL-FAILED" || summary.changed_after_signing) return "invalid";
  if (summary.worst_indication === "INDETERMINATE") return "unchecked";
  return summary.best_qualification === "qes" ? "qualified" : "valid";
}

export const BADGE_TONE: Record<SignatureBadgeKind, string> = {
  qualified: "ok",
  valid: "ok",
  invalid: "down",
  unchecked: "warn",
};

export function summaryForVersion(
  summaries: IncomingSignatureSummary[] | undefined,
  versionNumber: number
): IncomingSignatureSummary | undefined {
  return summaries?.find((s) => s.version_number === versionNumber);
}

// The signer's common name out of pyHanko's "Common Name: X, Organization: Y"
// rendering - the full subject stays available as a tooltip.
export function signerName(validation: IncomingSignatureValidation): string | null {
  const subject = validation.signer_subject;
  if (!subject) return null;
  const match = /Common Name:\s*([^,]+)/.exec(subject);
  return match ? match[1].trim() : subject;
}

const CHUNK = 200;

// Summaries for the documents of a list, in requests of at most 200 ids. A
// failure leaves the badges out - the list itself never depends on them.
export function useIncomingSignatureSummaries(
  token: string | null | undefined,
  documentIds: string[],
  reloadKey = 0
): Record<string, IncomingSignatureSummary[]> {
  const [summaries, setSummaries] = useState<Record<string, IncomingSignatureSummary[]>>({});
  const key = documentIds.join(",");
  useEffect(() => {
    if (!token || !key) {
      setSummaries({});
      return;
    }
    let cancelled = false;
    const ids = key.split(",");
    const chunks: string[][] = [];
    for (let i = 0; i < ids.length; i += CHUNK) chunks.push(ids.slice(i, i + CHUNK));
    Promise.all(chunks.map((chunk) => getIncomingSignatureSummaries(token, chunk)))
      .then((parts) => {
        if (!cancelled) setSummaries(Object.assign({}, ...parts));
      })
      .catch(() => {
        if (!cancelled) setSummaries({});
      });
    return () => {
      cancelled = true;
    };
  }, [token, key, reloadKey]);
  return summaries;
}
