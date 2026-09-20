"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { useAuth } from "@/lib/auth-context";

interface NavItem {
  href: string;
  labelKey: string;
  // Domain-separated admin roles (4.6, P6-S5): if the capability is
  // missing, the entry is hidden - defense in depth is additionally
  // provided by `RequireCapability` on the target page itself, in case the
  // URL is accessed directly. An array (Phase 52 Session 1) means "ANY of
  // these", matching `RequireCapability`'s own array form.
  requiresCapability?: string | string[];
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
      {
        href: "/object-types/",
        labelKey: "nav.objectTypes",
        requiresCapability: "admin.object_config",
      },
      {
        href: "/kennzeichen-settings/",
        labelKey: "nav.kennzeichenSettings",
        requiresCapability: "admin.object_config",
      },
      {
        href: "/config-packages/",
        labelKey: "nav.configPackages",
        requiresCapability: "admin.object_config",
      },
      {
        href: "/config-compare/",
        labelKey: "nav.configCompare",
        requiresCapability: "admin.object_config",
      },
      { href: "/registry/", labelKey: "nav.registry" },
      {
        href: "/teamspaces/",
        labelKey: "nav.teamspacesAdmin",
        requiresCapability: "admin.teamspace_management",
      },
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
      {
        href: "/upload-settings/",
        labelKey: "nav.uploadSettings",
        requiresCapability: "admin.document_config",
      },
      {
        href: "/export-settings/",
        labelKey: "nav.exportSettings",
        requiresCapability: "admin.document_config",
      },
      { href: "/processing-failures/", labelKey: "nav.processingFailures" },
      {
        href: "/signature-config/",
        labelKey: "nav.signatureConfig",
        requiresCapability: "admin.signature_config",
      },
    ],
  },
  {
    id: "storage",
    labelKey: "nav.groupStorage",
    items: [
      { href: "/storage-guard/", labelKey: "nav.storageGuard", requiresCapability: "admin.storage" },
      {
        href: "/storage-operational-config/",
        labelKey: "nav.storageOperationalConfig",
        requiresCapability: "admin.storage",
      },
    ],
  },
  {
    id: "compliance",
    labelKey: "nav.groupCompliance",
    items: [
      {
        href: "/retention-settings/",
        labelKey: "nav.retentionSettings",
        requiresCapability: "admin.retention",
      },
      { href: "/deletion-register/", labelKey: "nav.deletionRegister" },
      {
        href: "/archival-transfers/",
        labelKey: "nav.archivalTransfers",
        requiresCapability: "archival.read",
      },
    ],
  },
  {
    id: "security",
    labelKey: "nav.groupSecurity",
    items: [
      { href: "/superuser/", labelKey: "nav.superuser", requiresCapability: "breakglass.approve" },
      {
        href: "/forensic-trace/",
        labelKey: "nav.forensicTrace",
        requiresCapability: "reporting.forensic_trace",
      },
      {
        href: "/audit-trace-settings/",
        labelKey: "nav.auditTraceSettings",
        requiresCapability: "admin.document_config",
      },
      {
        href: "/share-link-settings/",
        labelKey: "nav.shareLinkSettings",
        requiresCapability: "admin.document_config",
      },
      {
        href: "/delegations/",
        labelKey: "nav.delegations",
        requiresCapability: "admin.user_management",
      },
      {
        href: "/approval-settings/",
        labelKey: "nav.approvalSettings",
        requiresCapability: "admin.user_management",
      },
      {
        href: "/ad-group-mappings/",
        labelKey: "nav.adGroupMappings",
        requiresCapability: "admin.user_management",
      },
      {
        href: "/user-tracking/",
        labelKey: "nav.userTracking",
        requiresCapability: ["admin.user_tracking", "admin.user_tracking_view"],
      },
      {
        href: "/email-templates/",
        labelKey: "nav.emailTemplates",
        requiresCapability: "admin.notification_config",
      },
    ],
  },
  {
    id: "reports",
    labelKey: "nav.groupReports",
    items: [{ href: "/reports/", labelKey: "nav.reports", requiresCapability: "reporting.read" }],
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

// Dashboard layout guiding principle (P4-S5, user feedback after P4-S3,
// concept 8): a left-hand, groupable/collapsible navigation sidebar instead
// of the previous flat top-nav links. Only two groups so far ("Management",
// "Installations"), but already built generically, since more groups will
// be added as functionality grows (later phases).
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
        const visibleItems = group.items.filter((item) => {
          if (!item.requiresCapability) return true;
          const required = Array.isArray(item.requiresCapability)
            ? item.requiresCapability
            : [item.requiresCapability];
          return required.some((c) => permissions.includes(c));
        });
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
