"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "@/i18n";
import { resolveLocaleFromDisplayLanguage, useLocale } from "@/lib/locale-context";
import { getHostDisplayLanguage, waitForOfficeReady } from "@/lib/office";

// Every interaction with `Office.context`/`Word.run` assumes that
// `Office.onReady()` has already resolved (office.js loads itself
// asynchronously, see layout.tsx <head> script tag) - this gate only
// renders the actual task pane content afterwards, identical principle
// to every official Office Add-in example.
//
// Since Phase 47 Session 2 (ADR 0167) this is also the one place that
// detects and applies Word's own display language
// (`Office.context.displayLanguage` is not valid before `Office.onReady()`
// resolves, same constraint as every other `Office.context` access) -
// `LocaleProvider` itself starts at `defaultLocale` and is switched here,
// exactly once, instead of exposing an in-app switcher.
export function OfficeGate({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const { setLocale } = useLocale();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    waitForOfficeReady()
      .then(() => {
        setLocale(resolveLocaleFromDisplayLanguage(getHostDisplayLanguage()));
        setReady(true);
      })
      .catch(() => setError(t("officeGate.error")));
  }, [t, setLocale]);

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
