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

// Cross-UI-Theming (8, P4-S6, Nutzer-Feedback nach P4-S5): identisches Muster
// wie in der User-UI (bewusst dupliziert statt geteilt, ADR 0006), da die
// Präferenz geräteübergreifend am Nutzerprofil hängt (`auth-service`
// `/me/preferences`, ADR 0009), nicht an einer einzelnen Installation.
// `accessToken` kommt hier aus dem installationsbezogenen `AuthProvider`
// (ADR 0008) - ein Wechsel der aktiven Installation liest also automatisch
// die Theme-Präferenz des dort angemeldeten Kontos nach.
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
        // Bewusst stillschweigend: der lokal gecachte Wert bleibt gültig,
        // falls die Server-Präferenz (noch) nicht lesbar ist.
      });
  }, [accessToken]);

  const setTheme = useCallback(
    (next: ThemeName) => {
      setThemeState(next);
      window.localStorage.setItem(STORAGE_KEY, next);
      if (accessToken) {
        updateThemePreference(accessToken, next).catch(() => {
          // Auswahl gilt sofort lokal weiter, auch wenn die Server-
          // Persistenz fehlschlägt - kein Retry in diesem Grundgerüst.
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
