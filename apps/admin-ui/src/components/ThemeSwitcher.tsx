"use client";

import { useI18n } from "@/i18n";
import type { ThemeName } from "@/lib/api";
import { useTheme } from "@/lib/theme-context";

const OPTIONS: ThemeName[] = ["auto", "light", "dark", "high-contrast"];

function labelKey(option: ThemeName): string {
  return option === "high-contrast" ? "highContrast" : option;
}

// `touch` (P83-S12): see LocaleSwitcher.
export function ThemeSwitcher({ touch = false }: { touch?: boolean }) {
  const { t } = useI18n();
  const { theme, setTheme } = useTheme();

  return (
    <label className={`flex items-center gap-2 text-sm text-fg${touch ? " justify-between" : ""}`}>
      {t("theme.label")}
      <select
        value={theme}
        onChange={(event) => setTheme(event.target.value as ThemeName)}
        className={`rounded-md border border-border bg-bg text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg ${touch ? "min-h-10 px-2" : "px-1 py-0.5"}`}
      >
        {OPTIONS.map((option) => (
          <option key={option} value={option}>
            {t(`theme.${labelKey(option)}`)}
          </option>
        ))}
      </select>
    </label>
  );
}
