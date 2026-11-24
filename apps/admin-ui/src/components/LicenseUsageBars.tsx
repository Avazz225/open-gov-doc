"use client";

import { useI18n } from "@/i18n";
import type { LicenseStatus } from "@/lib/api";

// Usage against each licence limit (P91-S6): a bar with the remaining
// headroom; amber from 80 %, red at the limit (new creations are blocked
// then, existing data isn't).

export const WARN_RATIO = 0.8;

type Dimension = "users" | "storage_gb" | "documents";
const DIMENSIONS: { key: Dimension; label: string }[] = [
  { key: "users", label: "license.users" },
  { key: "storage_gb", label: "license.storage" },
  { key: "documents", label: "license.documents" },
];

export function usageLevel(current: number, limit: number): "ok" | "warn" | "full" {
  if (limit <= 0 || current >= limit) return "full";
  return current / limit >= WARN_RATIO ? "warn" : "ok";
}

function format(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString("de-DE") : value.toLocaleString("de-DE", { maximumFractionDigits: 2 });
}

export function LicenseUsageBars({ status }: { status: LicenseStatus }) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="license-usage-heading" className="mb-4">
      <h2 id="license-usage-heading" className="text-base">
        {t("license.usageHeading")}
      </h2>
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {DIMENSIONS.map(({ key, label }) => {
          const value = status[key];
          const current = value?.current ?? null;
          const limit = value?.limit ?? null;
          if (current === null) {
            return (
              <li key={key} className="text-sm">
                <strong>{t(label)}:</strong> {t("license.usageUnknown")}
              </li>
            );
          }
          if (limit === null) {
            return (
              <li key={key} className="text-sm">
                <strong>{t(label)}:</strong> {format(current)} – {t("license.unlimited")}
              </li>
            );
          }
          const level = usageLevel(current, limit);
          const percent = Math.min(100, Math.round((current / Math.max(limit, 1)) * 100));
          const color =
            level === "full" ? "bg-[var(--dms-danger)]" : level === "warn" ? "bg-[var(--dms-warn)]" : "bg-accent";
          const remaining = Math.max(0, limit - current);
          return (
            <li key={key}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                <strong>{t(label)}</strong>
                <span>
                  {format(current)} / {format(limit)} ·{" "}
                  {level === "full"
                    ? t("license.limitReached")
                    : t("license.headroom", { remaining: format(remaining) })}
                </span>
              </div>
              <div
                role="progressbar"
                aria-label={t(label)}
                aria-valuemin={0}
                aria-valuemax={limit}
                aria-valuenow={current}
                aria-valuetext={`${format(current)} / ${format(limit)}`}
                className="mt-1 h-2 w-full overflow-hidden rounded-full bg-hover-bg"
              >
                <div className={`h-full ${color}`} style={{ width: `${percent}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
