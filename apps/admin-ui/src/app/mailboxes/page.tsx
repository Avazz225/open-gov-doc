"use client";

import { AdminShell } from "@/components/AdminShell";
import { MailboxSettings } from "@/components/MailboxSettings";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function MailboxesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.mail_config">
        <AdminShell title={t("mailboxes.pageTitle")}>
          <MailboxSettings />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
