"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";

interface NavItem {
  href: string;
  labelKey: string;
}

interface NavGroup {
  id: string;
  labelKey: string;
  items: NavItem[];
}

const GROUPS: NavGroup[] = [
  {
    id: "management",
    labelKey: "nav.groupManagement",
    items: [
      { href: "/users/", labelKey: "nav.users" },
      { href: "/object-types/", labelKey: "nav.objectTypes" },
      { href: "/registry/", labelKey: "nav.registry" },
    ],
  },
  {
    id: "installations",
    labelKey: "nav.groupInstallations",
    items: [{ href: "/installations/", labelKey: "nav.installations" }],
  },
];

const COLLAPSED_GROUPS_KEY = "dms.admin.collapsedGroups";

function loadCollapsedGroups(): Record<string, boolean> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(COLLAPSED_GROUPS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

// Dashboard-Layout-Leitbild (P4-S5, Nutzer-Feedback nach P4-S3, Konzept 8):
// linke, gruppierbare/ausklappbare Navigationsseitenleiste statt der
// vorherigen flachen Top-Nav-Links. Nur zwei Gruppen bisher ("Verwaltung",
// "Installationen"), aber bereits generisch gebaut, da mit wachsendem
// Funktionsumfang (spätere Phasen) weitere Gruppen dazukommen werden.
export function AdminSidebar() {
  const { t } = useI18n();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setCollapsed(loadCollapsedGroups());
  }, []);

  function toggleGroup(id: string) {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      window.localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify(next));
      return next;
    });
  }

  return (
    <nav className="admin-sidebar" aria-label={t("nav.ariaLabel")}>
      {GROUPS.map((group) => {
        const isCollapsed = Boolean(collapsed[group.id]);
        return (
          <div className="sidebar-group" key={group.id}>
            <button
              type="button"
              className="sidebar-group-toggle"
              aria-expanded={!isCollapsed}
              onClick={() => toggleGroup(group.id)}
            >
              <span aria-hidden="true">{isCollapsed ? "▸" : "▾"}</span> {t(group.labelKey)}
            </button>
            {!isCollapsed && (
              <ul className="sidebar-group-items">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={pathname === item.href ? "sidebar-link-active" : undefined}
                    >
                      {t(item.labelKey)}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </nav>
  );
}
