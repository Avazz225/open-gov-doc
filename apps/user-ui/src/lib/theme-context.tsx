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
import { getThemePreference, updateThemePreference, type ThemeName } from "./api";
import { useAuth } from "./auth-context";

const STORAGE_KEY = "dms.theme";
const THEMES: ThemeName[] = ["light", "dark", "high-contrast", "auto"];

function loadCachedTheme(): ThemeName {
  if (typeof window === "undefined") return "auto";
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return stored && THEMES.includes(stored as ThemeName) ? (stored as ThemeName) : "auto";
}

function resolveTheme(theme: ThemeName): "light" | "dark" | "high-contrast" {
  if (theme !== "auto") return theme;
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

interface ThemeContextValue {
  theme: ThemeName;
  setTheme: (theme: ThemeName) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

// Cross-UI theming (8, P4-S6, user feedback after P4-S5): light/dark/
// high-contrast/auto, synced across devices via the user profile (Keycloak
// attribute behind `auth-service` `/me/preferences`, see ADR 0009) instead
// of only locally in the browser. `localStorage` remains as an immediately
// available cache, so a later login doesn't have to wait for the first
// server response, and an unauthenticated state (login page) still has a
// theme.
export function ThemeProvider({ children }: { children: ReactNode }) {
  const { accessToken } = useAuth();
  const [theme, setThemeState] = useState<ThemeName>(() => loadCachedTheme());

  // `useLayoutEffect` instead of `useEffect`, so the attribute is set before
  // the first visible paint (no flash of the wrong theme).
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = resolveTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (theme !== "auto" || typeof window === "undefined") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => {
      document.documentElement.dataset.theme = resolveTheme("auto");
    };
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [theme]);

  useEffect(() => {
    if (!accessToken) return;
    getThemePreference(accessToken)
      .then((serverTheme) => {
        setThemeState(serverTheme);
        window.localStorage.setItem(STORAGE_KEY, serverTheme);
      })
      .catch(() => {
        // Deliberately silent: the locally cached value remains valid if the
        // server preference is not (yet) readable.
      });
  }, [accessToken]);

  const setTheme = useCallback(
    (next: ThemeName) => {
      setThemeState(next);
      window.localStorage.setItem(STORAGE_KEY, next);
      if (accessToken) {
        updateThemePreference(accessToken, next).catch(() => {
          // The selection continues to apply locally right away, even if
          // server-side persistence fails - no retry in this scaffold.
        });
      }
    },
    [accessToken]
  );

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme muss innerhalb von <ThemeProvider> verwendet werden");
  return context;
}
