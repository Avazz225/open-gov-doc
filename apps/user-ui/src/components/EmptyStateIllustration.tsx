"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useIllustrationTheme } from "@/lib/illustration-theme";

// Empty-state illustrations (P83-S4, ADR 0237): faux-UI stills from
// libs/dms-showcase, shown above a pane's own empty-state sentence. Purely
// decorative - the sentence carries the meaning, so the picture is
// aria-hidden and has no text alternative of its own. Rules:
// - gated by the width available *inside the pane* (dockview panes are
//   user-resizable), measured on this block itself, not the viewport. The
//   stage is 320px wide; from MIN_CONTENT_WIDTH it renders at >= 0.95 scale,
//   so its 1px lines and 14px icons stay crisp. Narrower, only the sentence
//   shows and the module is never loaded;
// - not rendered at all in the high-contrast theme;
// - stills, so reduced motion changes nothing;
// - the picture's box is reserved (aspect ratio) while its module loads, so
//   the sentence never jumps.

const STAGE_WIDTH = 320;
const STAGE_HEIGHT = 192;
const MIN_CONTENT_WIDTH = 304;

const ILLUSTRATIONS = {
  preview: dynamic(() => import("../../../../libs/dms-showcase/user-ui-empty-preview/EmptyPreview"), {
    ssr: false,
    loading: () => null,
  }),
  vorlagen: dynamic(() => import("../../../../libs/dms-showcase/user-ui-empty-vorlagen/EmptyVorlagen"), {
    ssr: false,
    loading: () => null,
  }),
};

export type EmptyStateKind = keyof typeof ILLUSTRATIONS;

/** Width of the element the ref is on; 0 until measured, and 0 where
 *  ResizeObserver does not exist (jsdom), so tests render the sentence only. */
function useElementWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** An empty state: the pane's own sentence, centered, with the
 *  illustration of `kind` above it when it fits. Without `kind` it is the
 *  same centered sentence, so neighbouring empty panes (e.g. preview and
 *  metadata) read as one family. */
export function EmptyState({ kind, children }: { kind?: EmptyStateKind; children: ReactNode }) {
  const [ref, width] = useElementWidth<HTMLDivElement>();
  const theme = useIllustrationTheme();
  const Illustration = kind ? ILLUSTRATIONS[kind] : null;
  const show = Illustration !== null && width >= MIN_CONTENT_WIDTH && theme !== "high-contrast";

  return (
    // gap-2: the stage keeps ~24px of shadow margin below its art, so the
    // sentence's ink sits ~32px under the picture - grouped with it
    <div ref={ref} className="flex flex-col items-center gap-2 py-8 text-center">
      {show && Illustration ? (
        <div
          aria-hidden="true"
          className="w-full max-w-[320px]"
          style={{ aspectRatio: `${STAGE_WIDTH} / ${STAGE_HEIGHT}` }}
          data-testid={`empty-illustration-${kind}`}
        >
          <Illustration theme={theme} />
        </div>
      ) : null}
      {children}
    </div>
  );
}
