"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { useIllustrationTheme, type IllustrationTheme } from "@/lib/illustration-theme";

// Login-page illustration (P83-S2, ADR 0237): a faux-UI still/animation built
// with the Stageshow kit in libs/dms-showcase. Purely illustrative - never a
// functional control, never fetches anything. Rules from ADR 0237:
// - rendered only from 1200px viewport width, so the 720px stage shows at
//   1:1 beside the login card; below that the module is never even loaded;
// - not rendered at all in the high-contrast theme;
// - reduced motion shows the poster frame (handled by the kit's Stage);
// - one text alternative for the whole picture, its content aria-hidden.

const MIN_WIDTH_QUERY = "(min-width: 1200px)";

const UserUiLoginIllustration = dynamic(
  () => import("../../../../libs/dms-showcase/user-ui-login/UserUiLogin"),
  { ssr: false, loading: () => null }
);

/** The viewport-width gate (starts "off", so tests and first paint render
 *  nothing) and the resolved theme. */
function useIllustrationEnvironment(): { wide: boolean; theme: IllustrationTheme } {
  const [wide, setWide] = useState(false);
  const theme = useIllustrationTheme();

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(MIN_WIDTH_QUERY);
    const update = () => setWide(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
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
        <UserUiLoginIllustration
          theme={theme}
          strings={{
            // The product's own labels, from the same keys the real Explorer uses
            pane: t("workspace.explorerPanelTitle"),
            root: t("folderBrowser.rootLabel"),
            upload: t("explorer.toggleUpload"),
            folder: t("login.illustration.folder"),
            file: t("login.illustration.file"),
            // Server-assigned reference numbers: the same in every language
            kennzeichen: "2026-114",
            neighbors: [
              ["2026-113", t("login.illustration.previousFile")],
              ["2026-112", t("login.illustration.olderFile")],
            ],
          }}
        />
      </div>
    </div>
  );
}
