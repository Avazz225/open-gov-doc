"use client";

import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import { getBrandingConfig } from "./api";
import { useInstallation } from "./installation-context";
import { useTheme } from "./theme-context";

interface BrandingContextValue {
  productName: string | null;
  accentColor: string | null;
  logoUrl: string | null;
}

const BrandingContext = createContext<BrandingContextValue>({
  productName: null,
  accentColor: null,
  logoUrl: null,
});

function hexToRgba(hex: string, alpha: number): string | null {
  const match = /^#([0-9a-fA-F]{6})$/.exec(hex);
  if (!match) return null;
  const int = parseInt(match[1], 16);
  const r = (int >> 16) & 255;
  const g = (int >> 8) & 255;
  const b = int & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Installation-level branding (7.3/8, P69-S2, ADR 0201): fetched once per
// active installation (re-fetches on `switchInstallation`, same trigger
// `ThemeProvider`'s own re-read on `accessToken` change uses), applied on
// top of the static `libs/dms-ui` tokens rather than extending that file
// itself (see the ADR - no per-installation override mechanism exists in
// `tokens.css`). Must be nested INSIDE `ThemeProvider` (see `layout.tsx`) -
// it reads the raw theme preference via `useTheme()` to skip the accent
// override in high-contrast mode, since that theme's accent values are a
// deliberate, legibility-driven fixed choice (`tokens.css`'s own comment
// on `--dms-accent-bg`'s high-contrast value), not a brand color to
// override. `"auto"` never resolves to high-contrast (see
// `theme-context.tsx`'s `resolveTheme`), so the raw preference is a safe
// proxy for the resolved theme here.
export function BrandingProvider({ children }: { children: ReactNode }) {
  const { activeInstallation } = useInstallation();
  const { theme } = useTheme();
  const [branding, setBranding] = useState<BrandingContextValue>({
    productName: null,
    accentColor: null,
    logoUrl: null,
  });

  useEffect(() => {
    let cancelled = false;
    getBrandingConfig()
      .then((config) => {
        if (cancelled) return;
        setBranding({
          productName: config.product_name,
          accentColor: config.accent_color,
          logoUrl: config.logo_url,
        });
      })
      .catch(() => {
        // Deliberately silent, same as ThemeProvider's own server-read
        // catch: an unreachable registry-service just means this
        // installation's build defaults keep applying, not an error state
        // worth surfacing to the person logging in.
      });
    return () => {
      cancelled = true;
    };
  }, [activeInstallation.gatewayBaseUrl]);

  useLayoutEffect(() => {
    if (branding.productName) {
      document.title = branding.productName;
    }
  }, [branding.productName]);

  useLayoutEffect(() => {
    const root = document.documentElement;
    if (!branding.accentColor || theme === "high-contrast") {
      root.style.removeProperty("--dms-accent");
      root.style.removeProperty("--dms-accent-bg");
      root.style.removeProperty("--dms-accent-bg-strong");
      return;
    }
    root.style.setProperty("--dms-accent", branding.accentColor);
    const bg = hexToRgba(branding.accentColor, 0.15);
    const bgStrong = hexToRgba(branding.accentColor, 0.5);
    if (bg) root.style.setProperty("--dms-accent-bg", bg);
    if (bgStrong) root.style.setProperty("--dms-accent-bg-strong", bgStrong);
  }, [branding.accentColor, theme]);

  return <BrandingContext.Provider value={branding}>{children}</BrandingContext.Provider>;
}

export function useBranding(): BrandingContextValue {
  return useContext(BrandingContext);
}
