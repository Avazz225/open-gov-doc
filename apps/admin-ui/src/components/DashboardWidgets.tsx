"use client";

import Link from "next/link";
import { GROUPS, visibleItems } from "@/components/AdminSidebar";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

// Role-dependent dashboard (Concept 8 "customizability... role-dependent
// dashboards", P69-S2, ADR 0201): reuses `AdminSidebar`'s own
// `GROUPS`/`visibleItems` data and capability filter verbatim, rather than
// inventing a second authorization mechanism - a group with no items
// visible to the current principal (e.g. a domain admin who only holds
// `admin.storage`) simply does not render a widget for it, same as it
// already does not render a sidebar entry for it.
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
    <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-4">
      {widgets.map(({ group, items }) => (
        <div className="card dashboard-widget" key={group.id}>
          <h2 className="mt-0 text-lg">{t(group.labelKey)}</h2>
          <ul className="m-0 pl-4">
            {items.map((item) => (
              <li key={item.href}>
                <Link href={item.href}>{t(item.labelKey)}</Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
