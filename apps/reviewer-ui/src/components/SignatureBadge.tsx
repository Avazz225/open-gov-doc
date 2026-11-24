"use client";

import { useI18n } from "@/i18n";
import type { IncomingSignatureSummary } from "@/lib/api";

// The signature state of a task document (P92-S3, ADR 0248 Decision 7) -
// same four states as the user UI's badge. Text carries the meaning; the
// colour only supports it.
type Kind = "qualified" | "valid" | "invalid" | "unchecked";

export function badgeKind(summary: IncomingSignatureSummary): Kind {
  if (summary.worst_indication === "TOTAL-FAILED" || summary.changed_after_signing) return "invalid";
  if (summary.worst_indication === "INDETERMINATE") return "unchecked";
  return summary.best_qualification === "qes" ? "qualified" : "valid";
}

const BASE = "badge-hc-border inline-block rounded-full px-2 py-[0.1rem] text-xs whitespace-nowrap";
const TONE: Record<Kind, string> = {
  qualified: "bg-success-bg text-success",
  valid: "bg-success-bg text-success",
  invalid: "bg-danger-bg text-danger",
  unchecked: "bg-accent-bg text-accent",
};

export function SignatureBadge({ summary }: { summary: IncomingSignatureSummary }) {
  const { t } = useI18n();
  const kind = badgeKind(summary);
  return (
    <span className={`${BASE} ${TONE[kind]}`} data-testid="signature-badge">
      {t(`signatureBadge.${kind}`)}
    </span>
  );
}
