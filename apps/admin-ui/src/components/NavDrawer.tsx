"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { X } from "lucide-react";

const FOCUSABLE = 'a[href], button:not([disabled]), select, input, textarea, [tabindex]:not([tabindex="-1"])';

// The sidebar as a modal drawer below 768px (P83-S12, ADR 0238 Decision 3).
// A dialog in the full sense: focus moves into it and is trapped there
// (Tab/Shift+Tab cycle), Escape and the backdrop close it, the page behind
// does not scroll, and focus returns to the button that opened it. It also
// closes itself when the window grows past 768px, where the sidebar column
// takes over - otherwise an invisible open drawer would keep the page
// scroll-locked.
export function NavDrawer({
  open,
  onClose,
  label,
  closeLabel,
  returnFocusRef,
  children,
}: {
  open: boolean;
  onClose: () => void;
  label: string;
  closeLabel: string;
  returnFocusRef: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  // The latest onClose without re-running the effect on every render.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
    focusables()[0]?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    // Optional: not every environment (jsdom) implements matchMedia.
    const wide = typeof window.matchMedia === "function" ? window.matchMedia("(min-width: 768px)") : null;
    function onWide(event: MediaQueryListEvent) {
      if (event.matches) onCloseRef.current();
    }

    document.addEventListener("keydown", onKeyDown);
    wide?.addEventListener("change", onWide);
    const returnTarget = returnFocusRef.current;
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      wide?.removeEventListener("change", onWide);
      document.body.style.overflow = previousOverflow;
      returnTarget?.focus();
    };
  }, [open, returnFocusRef]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 md:hidden">
      <div className="absolute inset-0 bg-black/40" aria-hidden="true" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        className="absolute inset-y-0 left-0 flex w-[min(300px,85vw)] flex-col overflow-y-auto bg-bg shadow-lg"
      >
        <div className="flex justify-end px-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            aria-label={closeLabel}
            className="flex min-h-10 min-w-10 items-center justify-center rounded-md border border-transparent bg-transparent text-fg transition-colors hover:bg-hover-bg"
          >
            <X size={20} strokeWidth={1.75} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
