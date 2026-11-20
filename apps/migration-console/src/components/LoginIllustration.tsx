"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";

// Login-page illustration (P83-S3, ADR 0237; host copied from user-ui's, P83-S2): a faux-UI still/animation built
// with the Stageshow kit in libs/dms-showcase. Purely illustrative - never a
// functional control, never fetches anything. Rules from ADR 0237:
// - rendered only from 1200px viewport width, so the 720px stage shows at
//   1:1 beside the login card; below that the module is never even loaded;
// - not rendered at all in the high-contrast theme;
// - reduced motion shows the poster frame (handled by the kit's Stage);
// - one text alternative for the whole picture, its content aria-hidden.

const MIN_WIDTH_QUERY = "(min-width: 1200px)";

const Illustration = dynamic(
  () => import("../../../../libs/dms-showcase/migration-console-login/MigrationConsoleLogin"),
  { ssr: false, loading: () => null }
);

type ResolvedTheme = "light" | "dark" | "high-contrast";

function readTheme(): ResolvedTheme {
  const value = document.documentElement.dataset.theme;
  if (value === "dark" || value === "high-contrast") return value;
  if (value === "light") return "light";
  // Before ThemeProvider has set data-theme: follow the system scheme, the
  // same fallback tokens.css itself applies.
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Follows the theme ThemeProvider writes to <html data-theme>, and the
 *  viewport-width gate. Both start "off" so tests (jsdom) and first paint
 *  render nothing. */
function useIllustrationEnvironment(): { wide: boolean; theme: ResolvedTheme } {
  const [wide, setWide] = useState(false);
  const [theme, setTheme] = useState<ResolvedTheme>("light");

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(MIN_WIDTH_QUERY);
    const update = () => setWide(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const update = () => setTheme(readTheme());
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);

  return { wide, theme };
}

export function LoginIllustration() {
  const { t } = useI18n();
  const { wide, theme } = useIllustrationEnvironment();

  if (!wide || theme === "high-contrast") return null;

  return (
    // -mt-6: the pane sits 24px into its stage (room for its shadow), so the
    // stage moves up by that much and the pane's top edge lines up exactly
    // with the login card's.
    <div role="img" aria-label={t("login.illustration.alt")} className="-mt-6 w-[720px] shrink-0">
      <div aria-hidden="true">
        <Illustration
          theme={theme}
          strings={{
            tabs: [t("nav.transfers"), t("nav.pairedInstallations")],
            target: t("login.illustration.target"),
            progress: t("transfers.documentsProgress", { copied: 2, total: 2, verified: 2 }),
            status: t("transfers.phaseReleased"),
            timeline: t("transfers.phasesHeading"),
            phases: [
              t("transfers.phaseCreated"),
              t("transfers.phaseLocked"),
              t("transfers.phaseCopied"),
              t("transfers.phaseVerified"),
              t("transfers.phaseReleased"),
            ],
            pendingPhase: t("transfers.phaseDeletionScheduled"),
          }}
        />
      </div>
    </div>
  );
}
