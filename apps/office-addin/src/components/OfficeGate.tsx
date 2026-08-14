"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { waitForOfficeReady } from "@/lib/office";

// Every interaction with `Office.context`/`Word.run` assumes that
// `Office.onReady()` has already resolved (office.js loads itself
// asynchronously, see layout.tsx <head> script tag) - this gate only
// renders the actual task pane content afterwards, identical principle
// to every official Office Add-in example.
export function OfficeGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    waitForOfficeReady()
      .then(() => setReady(true))
      .catch(() => setError(t("officeGate.error")));
  }, [t]);

  if (error) {
    return (
      <p className="page error-text" role="alert">
        {error}
      </p>
    );
  }
  if (!ready) {
    return <p className="page">{t("officeGate.loading")}</p>;
  }
  return <>{children}</>;
}
