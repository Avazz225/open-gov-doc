"use client";

import { useI18n, type Locale } from "@/i18n";
import { useLocale } from "@/lib/locale-context";

const OPTIONS: { value: Locale; label: string }[] = [
  { value: "de", label: "Deutsch" },
  { value: "en", label: "English" },
];

// `touch` (P83-S10): the larger form used inside the narrow-width account
// menu - label and control on one row across the menu's width, control at
// least 40px tall (ADR 0238 Decision 7).
export function LocaleSwitcher({ touch = false }: { touch?: boolean }) {
  const { t } = useI18n();
  const { locale, setLocale } = useLocale();
  return (
    <label className={`flex items-center gap-1 text-sm text-fg${touch ? " justify-between" : ""}`}>
      {t("locale.label")}
      <select
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        className={`rounded-md border border-border bg-bg text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg ${touch ? "min-h-10 px-2" : "px-1 py-0.5"}`}
      >
        {OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
