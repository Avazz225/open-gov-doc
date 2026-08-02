"use client";

import { createContext, useContext, type ReactNode } from "react";
import de from "./de.json";

// Vorbereitet für weitere Sprachen (Konzept 8, "Anpassbarkeit"), deckt aber
// bewusst erstmal nur Deutsch ab, identisches Muster wie user-ui/admin-ui.
export type Locale = "de";
export const defaultLocale: Locale = "de";

type Dictionary = typeof de;

const dictionaries: Record<Locale, Dictionary> = { de };

function resolve(dict: Dictionary, path: string): string {
  const value = path
    .split(".")
    .reduce<unknown>((node, key) => (node as Record<string, unknown> | undefined)?.[key], dict);
  return typeof value === "string" ? value : path;
}

interface I18nContextValue {
  locale: Locale;
  t: (path: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({
  children,
  locale = defaultLocale,
}: {
  children: ReactNode;
  locale?: Locale;
}) {
  const dict = dictionaries[locale] ?? dictionaries[defaultLocale];

  function t(path: string, vars?: Record<string, string | number>): string {
    let text = resolve(dict, path);
    if (vars) {
      for (const [key, value] of Object.entries(vars)) {
        text = text.replaceAll(`{${key}}`, String(value));
      }
    }
    return text;
  }

  return <I18nContext.Provider value={{ locale, t }}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n muss innerhalb von <I18nProvider> verwendet werden");
  return context;
}

export { de as defaultMessages };
