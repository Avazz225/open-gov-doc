"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

interface NavItem {
  href: string;
  labelKey: string;
  // Domänengetrennte Admin-Rollen (4.6, P6-S5): fehlt die Capability, wird
  // der Eintrag ausgeblendet - Tiefen-Verteidigung übernimmt zusätzlich
  // `RequireCapability` auf der Zielseite selbst, falls die URL direkt
  // aufgerufen wird.
  requiresCapability?: string;
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
      { href: "/users/", labelKey: "nav.users", requiresCapability: "admin.user_management" },
      { href: "/object-types/", labelKey: "nav.objectTypes" },
      { href: "/kennzeichen-settings/", labelKey: "nav.kennzeichenSettings" },
      {
        href: "/config-packages/",
        labelKey: "nav.configPackages",
        requiresCapability: "admin.object_config",
      },
      { href: "/registry/", labelKey: "nav.registry" },
    ],
  },
  {
    id: "installations",
    labelKey: "nav.groupInstallations",
    items: [{ href: "/installations/", labelKey: "nav.installations" }],
  },
  {
    id: "processing",
    labelKey: "nav.groupProcessing",
    items: [
      { href: "/ocr-settings/", labelKey: "nav.ocrSettings" },
      { href: "/upload-settings/", labelKey: "nav.uploadSettings" },
      { href: "/processing-failures/", labelKey: "nav.processingFailures" },
    ],
  },
  {
    id: "storage",
    labelKey: "nav.groupStorage",
    items: [{ href: "/storage-guard/", labelKey: "nav.storageGuard" }],
  },
  {
    id: "compliance",
    labelKey: "nav.groupCompliance",
    items: [
      { href: "/retention-settings/", labelKey: "nav.retentionSettings" },
      { href: "/deletion-register/", labelKey: "nav.deletionRegister" },
      { href: "/archival-transfers/", labelKey: "nav.archivalTransfers" },
    ],
  },
  {
    id: "security",
    labelKey: "nav.groupSecurity",
    items: [
      { href: "/superuser/", labelKey: "nav.superuser" },
      { href: "/forensic-trace/", labelKey: "nav.forensicTrace" },
      { href: "/audit-trace-settings/", labelKey: "nav.auditTraceSettings" },
      { href: "/share-link-settings/", labelKey: "nav.shareLinkSettings" },
      { href: "/delegations/", labelKey: "nav.delegations" },
      { href: "/approval-settings/", labelKey: "nav.approvalSettings" },
    ],
  },
  {
    id: "reports",
    labelKey: "nav.groupReports",
    items: [{ href: "/reports/", labelKey: "nav.reports" }],
  },
  {
    id: "diagnostics",
    labelKey: "nav.groupDiagnostics",
    items: [
      {
        href: "/query-console/",
        labelKey: "nav.queryConsole",
        requiresCapability: "admin.query_console",
      },
    ],
  },
  {
    id: "license",
    labelKey: "nav.groupLicense",
    items: [
      { href: "/license/", labelKey: "nav.license", requiresCapability: "admin.license" },
    ],
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
  const { permissions } = useAuth();
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
        const visibleItems = group.items.filter(
          (item) => !item.requiresCapability || permissions.includes(item.requiresCapability)
        );
        if (visibleItems.length === 0) return null;
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
                {visibleItems.map((item) => (
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
