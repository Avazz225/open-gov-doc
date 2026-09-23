"use client";

import { useI18n } from "@/i18n";
import type { ThemeName } from "@/lib/api";
import { useTheme } from "@/lib/theme-context";

const OPTIONS: ThemeName[] = ["auto", "light", "dark", "high-contrast"];

function labelKey(option: ThemeName): string {
  return option === "high-contrast" ? "highContrast" : option;
}

export function ThemeSwitcher() {
  const { t } = useI18n();
  const { theme, setTheme } = useTheme();

  return (
    <label className="flex flex-col gap-1 text-sm text-fg">
      {t("theme.label")}
      <select
        value={theme}
        onChange={(event) => setTheme(event.target.value as ThemeName)}
        className="rounded-md border border-border bg-bg px-1 py-0.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
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
