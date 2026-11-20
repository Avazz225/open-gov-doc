"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, CircleUserRound } from "lucide-react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeSwitcher } from "./ThemeSwitcher";

// The account menu (P86-S1, ADR 0238 Decision 2 as amended): the header shows
// only the signed-in user's name with a chevron, at every width. It opens a
// dropdown with the settings - app-specific controls first (`leading`, e.g.
// admin-ui's installation switcher), then language, theme and log out, each
// at least 40px tall. The former inline cluster (P83-S7 item 10) and its
// breakpoint switch (`inlineFrom`, P83-S10/S11) are gone.
// Duplicated per app on purpose (ADR 0006: no shared JS between apps); the
// same file exists in every app that has this header.
export function AccountMenu({ leading }: { leading?: (touch: boolean) => ReactNode }) {
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

  return (
    <div ref={rootRef} className="relative flex-none">
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={t("account.menuLabel", { name: user?.username ?? "" })}
        onClick={() => setOpen((value) => !value)}
        className={`flex min-h-10 min-w-10 items-center justify-center gap-1.5 rounded-md border border-transparent px-2 text-fg transition-colors hover:bg-hover-bg ${open ? "bg-hover-bg" : "bg-transparent"}`}
      >
        <CircleUserRound size={20} strokeWidth={1.75} aria-hidden="true" className="flex-none" />
        {/* The name is the label people look for; a long one is cut, and
            below 640px it yields to the icon so the header row keeps fitting. */}
        {user && (
          <span className="hidden max-w-[12rem] truncate text-sm sm:inline">{user.username}</span>
        )}
        <ChevronDown
          size={14}
          strokeWidth={1.75}
          aria-hidden="true"
          className={`flex-none opacity-60 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div
          id={panelId}
          className="absolute right-0 top-full z-50 mt-2 flex w-64 flex-col gap-3 rounded-lg border border-border bg-bg p-3 shadow-lg"
        >
          {user && (
            <p className="m-0 truncate border-b border-border pb-2 font-semibold" title={user.username}>
              {user.username}
            </p>
          )}
          {leading?.(true)}
          <LocaleSwitcher touch />
          <ThemeSwitcher touch />
          <button
            type="button"
            onClick={logout}
            className="min-h-10 w-full whitespace-nowrap rounded-md border border-border bg-hover-bg px-3 py-1 text-fg transition-colors hover:bg-accent-bg"
          >
            {t("common.logout")}
          </button>
        </div>
      )}
    </div>
  );
}
