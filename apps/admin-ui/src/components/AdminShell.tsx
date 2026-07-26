"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

export function AdminShell({ title, children }: { title: string; children: ReactNode }) {
  const { user, logout } = useAuth();
  const { t } = useI18n();

  return (
    <main className="page">
      <div className="top-bar">
        <h1>{title}</h1>
        <div>
          {user && <span>{user.username} </span>}
          <button type="button" onClick={logout}>
            {t("common.logout")}
          </button>
        </div>
      </div>
      <nav className="admin-nav" aria-label={t("nav.ariaLabel")}>
        <Link href="/users/">{t("nav.users")}</Link>
        <Link href="/object-types/">{t("nav.objectTypes")}</Link>
        <Link href="/registry/">{t("nav.registry")}</Link>
      </nav>
      {children}
    </main>
  );
}
