"use client";

import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import { defaultLocale, I18nProvider, type Locale } from "@/i18n";

const LOCALES: Locale[] = ["de", "en"];

// UI display language (8, Phase 47 Session 2, ADR 0167): this add-in
// follows Word's own display language automatically rather than gaining
// an in-app switcher (space constraints, and a document editor showing UI
// text in a language different from the surrounding Word chrome would be
// actively confusing - the same "follow the host" reasoning already
// applied to this app's missing theme switcher, see
// docs/services/office-addin.md). `Office.context.displayLanguage` is a
// BCP-47 tag (e.g. "de-DE", "en-US") - only the primary language subtag
// is matched against the two dictionaries that actually exist; anything
// else (a third language Word might report) falls back to `defaultLocale`
// rather than rendering an empty/broken dictionary.
export function resolveLocaleFromDisplayLanguage(displayLanguage: string | undefined): Locale {
  if (!displayLanguage) return defaultLocale;
  const primary = displayLanguage.split("-")[0].toLowerCase();
  return LOCALES.includes(primary as Locale) ? (primary as Locale) : defaultLocale;
}

interface LocaleContextValue {
  locale: Locale;
  // Not a user-facing switcher - called exactly once by `OfficeGate` after
  // `Office.onReady()` resolves (`Office.context.displayLanguage` is not
  // valid before that), see `OfficeGate.tsx`.
  setLocale: (locale: Locale) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  // Starts at `defaultLocale` for the same hydration-safety reason as the
  // other apps' `LocaleProvider` (this is a static export that always
  // server-renders `defaultLocale`'s strings) - here host detection can
  // only run after `Office.onReady()` anyway, so the constraint is
  // automatically satisfied rather than needing a post-mount `useEffect`.
  const [locale, setLocale] = useState<Locale>(defaultLocale);

  useLayoutEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value: LocaleContextValue = { locale, setLocale };

  return (
    <LocaleContext.Provider value={value}>
      <I18nProvider locale={locale}>{children}</I18nProvider>
    </LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale muss innerhalb von <LocaleProvider> verwendet werden");
  return context;
}
