"use client";

import { AdminShell } from "@/components/AdminShell";
import { RequireAuth } from "@/components/RequireAuth";
import { SignatureConfig } from "@/components/SignatureConfig";
import { useI18n } from "@/i18n";

export default function SignatureConfigPage() {
  const { t } = useI18n();
  return (
    <RequireAuth>
      <AdminShell title={t("signatureConfig.pageTitle")}>
        <SignatureConfig />
      </AdminShell>
    </RequireAuth>
  );
}
