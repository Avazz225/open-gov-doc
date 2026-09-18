"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { defaultLocale, I18nProvider, type Locale } from "@/i18n";
import { getLocalePreference, updateLocalePreference } from "./api";
import { useAuth } from "./auth-context";

const STORAGE_KEY = "dms.locale";
const LOCALES: Locale[] = ["de", "en"];

function loadCachedLocale(): Locale | null {
  if (typeof window === "undefined") return null;
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored && LOCALES.includes(stored as Locale) ? (stored as Locale) : null;
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

// UI display language (8, Phase 47 Session 3) - same cross-device pattern
// as `ThemeProvider` (ADR 0009's `/me/preferences`), a stateful sibling
// instead of `I18nProvider`'s previous static `locale` prop. Renders
// `I18nProvider` itself, so this is now the single place that owns the
// current locale.
export function LocaleProvider({ children }: { children: ReactNode }) {
  const { accessToken } = useAuth();
  // Unlike `ThemeProvider`, which can safely read `localStorage` in the
  // initial `useState` (theme only drives an imperative `dataset.theme`
  // attribute, never text React itself renders): the static export
  // always server-renders `defaultLocale`'s strings, and `locale` here
  // drives the actual `t()`-rendered text tree. Starting from a cached
  // non-default value would mismatch that markup on hydration (React
  // error #418, found live in `process-designer`'s P47-S1) - so the first
  // client render matches the server exactly, and the cached choice is
  // applied only after mount below.
  const [locale, setLocaleState] = useState<Locale>(defaultLocale);

  useEffect(() => {
    const cached = loadCachedLocale();
    if (cached && cached !== defaultLocale) setLocaleState(cached);
  }, []);

  // `useLayoutEffect` like `ThemeProvider`'s `dataset.theme` - keeps
  // `document.documentElement.lang` in sync before first paint.
  useLayoutEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  useEffect(() => {
    if (!accessToken) return;
    getLocalePreference(accessToken)
      .then((serverLocale) => {
        setLocaleState(serverLocale);
        window.localStorage.setItem(STORAGE_KEY, serverLocale);
      })
      .catch(() => {
        // Deliberately silent, same reasoning as ThemeProvider: the
        // locally cached value remains valid.
      });
  }, [accessToken]);

  const setLocale = useCallback(
    (next: Locale) => {
      setLocaleState(next);
      window.localStorage.setItem(STORAGE_KEY, next);
      if (accessToken) {
        updateLocalePreference(accessToken, next).catch(() => {
          // Selection continues to apply locally immediately, even if
          // server persistence fails - no retry, same as ThemeProvider.
        });
      }
    },
    [accessToken]
  );

  return (
    <LocaleContext.Provider value={{ locale, setLocale }}>
      <I18nProvider locale={locale}>{children}</I18nProvider>
    </LocaleContext.Provider>
  );
}

export function useLocale(): LocaleContextValue {
  const context = useContext(LocaleContext);
  if (!context) throw new Error("useLocale muss innerhalb von <LocaleProvider> verwendet werden");
  return context;
}
