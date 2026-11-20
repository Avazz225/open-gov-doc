"use client";

import { useEffect, useState } from "react";

// The resolved theme for libs/dms-showcase illustrations (ADR 0237), shared by
// LoginIllustration (P83-S2) and EmptyStateIllustration (P83-S4). Follows the
// theme ThemeProvider writes to <html data-theme>, at runtime.

export type IllustrationTheme = "light" | "dark" | "high-contrast";

function readTheme(): IllustrationTheme {
  const value = document.documentElement.dataset.theme;
  if (value === "dark" || value === "high-contrast") return value;
  if (value === "light") return "light";
  // Before ThemeProvider has set data-theme: follow the system scheme, the
  // same fallback tokens.css itself applies.
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function useIllustrationTheme(): IllustrationTheme {
  const [theme, setTheme] = useState<IllustrationTheme>("light");

  useEffect(() => {
    const update = () => setTheme(readTheme());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return theme;
}
