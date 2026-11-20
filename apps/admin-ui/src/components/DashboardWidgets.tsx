"use client";

import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { GROUPS, visibleItems } from "@/components/AdminSidebar";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// The home page's area launcher (P83-S7, craft-lens punch list item 7).
//
// It does repeat the sidebar's areas - deliberately kept rather than dropped:
// with narrow-width support as a goal (ADR 0237 Decision 6), the sidebar
// collapses on small screens and this launcher becomes the way in. What the
// review actually objected to was unstyled, underlined, bulleted link lists;
// these are now cards in the shared material with tappable rows (>= 40px,
// hover fill, a chevron for "leads on"), the same visibility rules as the
// sidebar.
export function DashboardWidgets() {
  const { t } = useI18n();
  const { permissions } = useAuth();

  const widgets = GROUPS.map((group) => ({
    group,
    items: visibleItems(group.items, permissions),
  })).filter((w) => w.items.length > 0);

  if (widgets.length === 0) {
    return <p className="italic opacity-70">{t("home.noWidgets")}</p>;
  }

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
      {widgets.map(({ group, items }) => (
        <section
          className="rounded-lg border border-border bg-surface p-2 shadow-sm"
          key={group.id}
          aria-labelledby={`launcher-${group.id}`}
        >
          <h2 id={`launcher-${group.id}`} className="m-0 px-3 pt-2 pb-1 text-sm font-semibold text-surface-fg">
            {t(group.labelKey)}
          </h2>
          <ul className="m-0 list-none p-0">
            {items.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="flex min-h-10 items-center justify-between gap-3 rounded-md px-3 py-2 text-sm text-fg no-underline transition-colors hover:bg-hover-bg focus-visible:outline-2 focus-visible:outline-accent"
                >
                  <span>{t(item.labelKey)}</span>
                  <ChevronRight size={16} strokeWidth={1.75} aria-hidden="true" className="flex-none opacity-50" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
