"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, CircleUserRound } from "lucide-react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeSwitcher } from "./ThemeSwitcher";

// The header cluster - language, theme, signed-in user, log out (P83-S7
// item 10) - in its two forms (P83-S10, ADR 0238 Decision 2):
// - from 768px (`md`) inline, as before;
// - below 768px one account button that opens a popover with the same four
//   controls stacked, each at least 40px tall.
// Both forms are rendered and switched by CSS; `display: none` keeps the
// hidden one out of the accessibility tree and the tab order.
// `inlineFrom="lg"` (P83-S11) moves the switch to 1024px for a header whose
// row is too full at 768px (reviewer-ui: four tabs plus the cluster).
// `leading` (P83-S12) renders app-specific controls first in both forms -
// admin-ui's installation switcher; it receives `touch` for the popover.
// Duplicated per app on purpose (ADR 0006: no shared JS between apps); the
// same file exists in every app that has this header.
// Literal class names per breakpoint - Tailwind only generates classes it
// finds spelled out in the source.
const BREAKPOINT_CLASSES = {
  md: { inline: "hidden md:flex", menu: "md:hidden" },
  lg: { inline: "hidden lg:flex", menu: "lg:hidden" },
} as const;

export function AccountMenu({
  inlineFrom = "md",
  leading,
}: {
  inlineFrom?: "md" | "lg";
  leading?: (touch: boolean) => ReactNode;
}) {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const logoutBtn =
    "whitespace-nowrap rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg";

  return (
    <>
      <div className={`${BREAKPOINT_CLASSES[inlineFrom].inline} flex-none items-center gap-4`}>
        {leading?.(false)}
        <LocaleSwitcher />
        <ThemeSwitcher />
        {user && <span className="whitespace-nowrap">{user.username}</span>}
        <button type="button" onClick={logout} className={logoutBtn}>
          {t("common.logout")}
        </button>
      </div>

      <div ref={rootRef} className={`relative flex-none ${BREAKPOINT_CLASSES[inlineFrom].menu}`}>
        <button
          ref={buttonRef}
          type="button"
          aria-haspopup="true"
          aria-expanded={open}
          aria-controls={panelId}
          aria-label={t("account.menuLabel", { name: user?.username ?? "" })}
          onClick={() => setOpen((value) => !value)}
          className="flex min-h-10 min-w-10 items-center justify-center gap-1 rounded-md border border-transparent bg-transparent px-2 text-fg transition-colors hover:bg-hover-bg"
        >
          <CircleUserRound size={20} strokeWidth={1.75} aria-hidden="true" />
          <ChevronDown size={14} strokeWidth={1.75} aria-hidden="true" className="opacity-60" />
        </button>
        {open && (
          <div
            id={panelId}
            className="absolute right-0 top-full z-50 mt-2 flex w-64 flex-col gap-3 rounded-lg border border-border bg-bg p-3 shadow-lg"
          >
            {user && <p className="m-0 truncate font-semibold">{user.username}</p>}
            {leading?.(true)}
            <LocaleSwitcher touch />
            <ThemeSwitcher touch />
            <button type="button" onClick={logout} className={`${logoutBtn} min-h-10 w-full`}>
              {t("common.logout")}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
