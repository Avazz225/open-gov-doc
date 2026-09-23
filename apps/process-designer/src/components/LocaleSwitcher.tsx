"use client";

import { useI18n, type Locale } from "@/i18n";
import { useLocale } from "@/lib/locale-context";

const OPTIONS: { value: Locale; label: string }[] = [
  { value: "de", label: "Deutsch" },
  { value: "en", label: "English" },
];

export function LocaleSwitcher() {
  const { t } = useI18n();
  const { locale, setLocale } = useLocale();
  return (
    <label className="flex flex-col gap-1 text-sm text-fg">
      {t("locale.label")}
      <select
        value={locale}
        onChange={(event) => setLocale(event.target.value as Locale)}
        className="rounded-md border border-border bg-bg px-1 py-0.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
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
