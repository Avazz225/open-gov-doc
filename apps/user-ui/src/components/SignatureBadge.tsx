"use client";

import { useI18n } from "@/i18n";
import type { IncomingSignatureSummary } from "@/lib/api";
import { BADGE_TONE, badgeKind } from "@/lib/incoming-signatures";

// The signature state of a document version in lists (P92-S3): qualified,
// valid, invalid (failed or changed after signing) or not checkable. Text,
// not colour alone, carries the meaning.
export function SignatureBadge({ summary }: { summary: IncomingSignatureSummary }) {
  const { t } = useI18n();
  const kind = badgeKind(summary);
  return (
    <span
      className={`badge ${BADGE_TONE[kind]} signature-badge`}
      title={t(`incomingSignatures.badgeHint.${kind}`, {
        count: summary.signature_count,
        date: new Date(summary.validated_at).toLocaleString(),
      })}
      data-testid="signature-badge"
    >
      {t(`incomingSignatures.badge.${kind}`)}
    </span>
  );
}
