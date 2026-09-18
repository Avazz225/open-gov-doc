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
    <label className="locale-switcher">
      {t("locale.label")}
      <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
        {OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
