"use client";

import { useEffect, useState } from "react";
import {
  AlarmClock,
  Archive,
  Briefcase,
  Contact,
  FileLock,
  FileText,
  FolderTree,
  Folders,
  Inbox,
  ListChecks,
  Search,
  ShieldAlert,
  Star,
  Trash2,
  UserCheck,
  Users,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/i18n";
import { UiIcon } from "@/lib/ui-icons";
import { countDueReminders } from "@/lib/api";
import { REMINDERS_CHANGED_EVENT } from "@/lib/reminders";

export type WorkspaceView =
  | "documents"
  | "search"
  | "approvals"
  | "favorites"
  | "reminders"
  | "teamspaces"
  | "delegations"
  | "trash"
  | "quarantine"
  | "poststelle"
  | "kontakte"
  | "aussonderung"
  | "vorlagen"
  | "cases"
  | "handFolders"
  | "recordsQuarantine";

// Quarantine area (2.5/10.3, P15-S2) - unlike the trash (always visible at
// least in the personal view), the concept specifies NO generally
// accessible view here: "a dedicated, narrowly scoped role may view a
// quarantine case" - the icon rail entry itself stays invisible to all
// other roles, not just the actions within it (same independently
// configurable role-setting pattern used throughout the project).
const QUARANTINE_ADMIN_ROLE = "dms-admin";

// Inbox/outbox (2.5/3.3, P15-S3) - same pattern: "Only a dedicated mail
// room role sees/processes the unscreened incoming items."
const POSTSTELLE_ROLE = "dms-poststelle";

// Records disposal access area (2.5/5.6, P15-S5) - same role gate as
// archival-services `archive_retrieval_role` (concept 2.5: "a dedicated
// archive/registry role"), unlike the ungated view of the contacts area.
const ARCHIVAL_ACCESS_ROLE = "dms-admin";

// Far left edge, outside the three-way split main content (user feedback
// after P4-S3, 8): icon-based cross-cutting navigation. "Dokumente"
// and, since P5-S4, also "Suche" toggle between the two views in
// DocumentWorkspace. Since P4-S6, "Einstellungen" opens a popover with the
// theme switcher instead of remaining disabled.
export function IconRail({
  activeView,
  onSelectView,
}: {
  activeView: WorkspaceView;
  onSelectView: (view: WorkspaceView) => void;
}) {
  const { t } = useI18n();
  const { user, permissions } = useAuth();
  const isQuarantineAdmin = Boolean(user?.realm_roles.includes(QUARANTINE_ADMIN_ROLE));
  const isPoststelle = Boolean(user?.realm_roles.includes(POSTSTELLE_ROLE));
  const isArchivalAccess = Boolean(user?.realm_roles.includes(ARCHIVAL_ACCESS_ROLE));
  // Records quarantine (14.2, ADR 0116), since Post-Roadmap Phase 36
  // Session 2 - the system-native `permissions` capability check
  // `RecordsQuarantinePanel.tsx` already uses, not a realm role (unlike the
  // unrelated virus-scan "quarantine" view above).
  const isRecordsQuarantineAdmin = permissions.includes("admin.records_quarantine");
  const dueReminders = useDueReminderCount(activeView);

  return (
    <nav
      className="flex w-12 flex-shrink-0 flex-col items-center gap-2 border-r border-border py-3"
      aria-label={t("iconRail.label")}
    >
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "documents" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.documents")}
        aria-current={activeView === "documents" ? "page" : undefined}
        onClick={() => onSelectView("documents")}
      >
        <UiIcon icon={FileText} />
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "search" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.search")}
        aria-current={activeView === "search" ? "page" : undefined}
        onClick={() => onSelectView("search")}
      >
        <UiIcon icon={Search} />
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "approvals" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.approvals")}
        aria-current={activeView === "approvals" ? "page" : undefined}
        onClick={() => onSelectView("approvals")}
      >
        <UiIcon icon={ListChecks} />
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "favorites" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.favorites")}
        aria-current={activeView === "favorites" ? "page" : undefined}
        onClick={() => onSelectView("favorites")}
      >
        <UiIcon icon={Star} />
      </button>
      {/* Follow-up reminders (P90-S2, ADR 0246) - the badge counts the due
          ones; it is the reminders' main signal, since no view lists a
          person's own in-app notifications. */}
      <button
        type="button"
        className={`relative grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "reminders" ? "bg-accent-bg" : "bg-transparent"}`}
        title={
          dueReminders > 0
            ? t("iconRail.remindersDue", { count: dueReminders })
            : t("iconRail.reminders")
        }
        aria-label={
          dueReminders > 0
            ? t("iconRail.remindersDue", { count: dueReminders })
            : t("iconRail.reminders")
        }
        aria-current={activeView === "reminders" ? "page" : undefined}
        onClick={() => onSelectView("reminders")}
      >
        <UiIcon icon={AlarmClock} />
        {dueReminders > 0 && (
          <span className="rail-badge" aria-hidden="true">
            {dueReminders > 99 ? "99+" : dueReminders}
          </span>
        )}
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "teamspaces" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.teamspaces")}
        aria-current={activeView === "teamspaces" ? "page" : undefined}
        onClick={() => onSelectView("teamspaces")}
      >
        <UiIcon icon={Users} />
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "delegations" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.delegations")}
        aria-current={activeView === "delegations" ? "page" : undefined}
        onClick={() => onSelectView("delegations")}
      >
        <UiIcon icon={UserCheck} />
      </button>
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "trash" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.trash")}
        aria-current={activeView === "trash" ? "page" : undefined}
        onClick={() => onSelectView("trash")}
      >
        <UiIcon icon={Trash2} />
      </button>
      {isQuarantineAdmin && (
        <button
          type="button"
          className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "quarantine" ? "bg-accent-bg" : "bg-transparent"}`}
          title={t("iconRail.quarantine")}
          aria-current={activeView === "quarantine" ? "page" : undefined}
          onClick={() => onSelectView("quarantine")}
        >
          <UiIcon icon={ShieldAlert} />
        </button>
      )}
      {isRecordsQuarantineAdmin && (
        <button
          type="button"
          className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "recordsQuarantine" ? "bg-accent-bg" : "bg-transparent"}`}
          title={t("iconRail.recordsQuarantine")}
          aria-current={activeView === "recordsQuarantine" ? "page" : undefined}
          onClick={() => onSelectView("recordsQuarantine")}
        >
          <UiIcon icon={FileLock} />
        </button>
      )}
      {isPoststelle && (
        <button
          type="button"
          className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "poststelle" ? "bg-accent-bg" : "bg-transparent"}`}
          title={t("iconRail.poststelle")}
          aria-current={activeView === "poststelle" ? "page" : undefined}
          onClick={() => onSelectView("poststelle")}
        >
          <UiIcon icon={Inbox} />
        </button>
      )}
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "kontakte" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.kontakte")}
        aria-current={activeView === "kontakte" ? "page" : undefined}
        onClick={() => onSelectView("kontakte")}
      >
        <UiIcon icon={Contact} />
      </button>
      {isArchivalAccess && (
        <button
          type="button"
          className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "aussonderung" ? "bg-accent-bg" : "bg-transparent"}`}
          title={t("iconRail.aussonderung")}
          aria-current={activeView === "aussonderung" ? "page" : undefined}
          onClick={() => onSelectView("aussonderung")}
        >
          <UiIcon icon={Archive} />
        </button>
      )}
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "vorlagen" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.vorlagen")}
        aria-current={activeView === "vorlagen" ? "page" : undefined}
        onClick={() => onSelectView("vorlagen")}
      >
        <UiIcon icon={FolderTree} />
      </button>
      {/* Minimal case-browsing UI (14.2, post-roadmap phase 34 session 3,
          ADR 0141) - ungated (`case.read` is granted to "everyone" by
          default, ADR 0070; the export/import actions within the pane are
          individually gated on `archival.write`, not the pane itself). */}
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "cases" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.cases")}
        aria-current={activeView === "cases" ? "page" : undefined}
        onClick={() => onSelectView("cases")}
      >
        <UiIcon icon={Briefcase} />
      </button>
      {/* Hand-folder/work-tray cross-installation overview (14.2, Post-
          Roadmap Phase 35 Session 4, ADR 0146) - ungated, same posture as
          "cases": the underlying data is already permission-filtered
          per-row (folder.read for references, the existing document.read
          check for work-tray documents), not a capability check on the
          pane itself. */}
      <button
        type="button"
        className={`grid w-9 h-9 place-items-center rounded-md border-0 p-0 text-fg cursor-pointer disabled:opacity-35 disabled:cursor-not-allowed ${activeView === "handFolders" ? "bg-accent-bg" : "bg-transparent"}`}
        title={t("iconRail.handFolders")}
        aria-current={activeView === "handFolders" ? "page" : undefined}
        onClick={() => onSelectView("handFolders")}
      >
        <UiIcon icon={Folders} />
      </button>
    </nav>
  );
}

// Due follow-up reminders for the badge (P90-S2): loaded on mount, every
// minute (the server's due poll runs on the same cadence), whenever the view
// changes, and when a reminder is changed anywhere in the app. A failed load
// keeps the last count - the badge is a hint, not a source of truth.
const DUE_REFRESH_MS = 60_000;

function useDueReminderCount(activeView: WorkspaceView): number {
  const { accessToken, user } = useAuth();
  const username = user?.username ?? "";
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!accessToken || !username) return;
    let cancelled = false;
    const refresh = () => {
      countDueReminders(accessToken, username)
        .then((due) => {
          if (!cancelled) setCount(due);
        })
        .catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, DUE_REFRESH_MS);
    window.addEventListener(REMINDERS_CHANGED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener(REMINDERS_CHANGED_EVENT, refresh);
    };
  }, [accessToken, username, activeView]);

  return count;
}
