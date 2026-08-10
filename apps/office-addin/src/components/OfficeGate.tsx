"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { waitForOfficeReady } from "@/lib/office";

// Jede Interaktion mit `Office.context`/`Word.run` setzt voraus, dass
// `Office.onReady()` bereits aufgelöst hat (office.js lädt sich selbst
// asynchron, siehe layout.tsx <head>-Script-Tag) - dieses Gate rendert den
// eigentlichen Taskpane-Inhalt erst danach, identisches Prinzip wie jedes
// offizielle Office-Add-in-Beispiel.
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
