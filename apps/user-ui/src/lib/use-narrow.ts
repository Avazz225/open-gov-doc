"use client";

import { useEffect, useState } from "react";

// Narrow = below 768px, ADR 0238 Decision 1 (Tailwind's `md`). P83-S13:
// user-ui switches structure there (single-pane workspace), which CSS alone
// can't do. Starts `false` so the static export's first paint matches the
// server-rendered desktop markup; the real value arrives after mount.
export const NARROW_QUERY = "(max-width: 767.98px)";

export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(NARROW_QUERY);
    setNarrow(media.matches);
    const onChange = (event: MediaQueryListEvent) => setNarrow(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return narrow;
}
