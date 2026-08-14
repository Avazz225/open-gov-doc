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

// Cross-UI theming (8, P4-S6, user feedback after P4-S5): identical pattern
// to the user UI (deliberately duplicated instead of shared, ADR 0006), since the
// preference is tied to the user profile across devices (`auth-service`
// `/me/preferences`, ADR 0009), not to a single installation.
// `accessToken` here comes from the installation-scoped `AuthProvider`
// (ADR 0008) - switching the active installation therefore automatically
// re-reads the theme preference of the account logged in there.
export function ThemeProvider({ children }: { children: ReactNode }) {
  const { accessToken } = useAuth();
  const [theme, setThemeState] = useState<ThemeName>(() => loadCachedTheme());

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
        // Deliberately silent: the locally cached value remains valid
        // if the server preference is (not yet) readable.
      });
  }, [accessToken]);

  const setTheme = useCallback(
    (next: ThemeName) => {
      setThemeState(next);
      window.localStorage.setItem(STORAGE_KEY, next);
      if (accessToken) {
        updateThemePreference(accessToken, next).catch(() => {
          // Selection continues to apply locally immediately, even if the server
          // persistence fails - no retry in this base scaffold.
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
