"use client";

import { AdminShell } from "@/components/AdminShell";
import { EmailTemplates } from "@/components/EmailTemplates";
import { RequireAuth } from "@/components/RequireAuth";
import { useI18n } from "@/i18n";

export default function EmailTemplatesPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("emailTemplates.pageTitle")}>
        <EmailTemplates />
      </AdminShell>
    </RequireAuth>
  );
}
