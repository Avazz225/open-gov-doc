"use client";

import { AdminShell } from "@/components/AdminShell";
import { EmailTemplates } from "@/components/EmailTemplates";
import { RequireAuth } from "@/components/RequireAuth";
import { RequireCapability } from "@/components/RequireCapability";
import { useI18n } from "@/i18n";

export default function EmailTemplatesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <RequireCapability capability="admin.notification_config">
        <AdminShell title={t("emailTemplates.pageTitle")}>
          <EmailTemplates />
        </AdminShell>
      </RequireCapability>
    </RequireAuth>
  );
}
