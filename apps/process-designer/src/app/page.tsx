"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import { DmnDefinitionList } from "@/components/DmnDefinitionList";
import { ProcessDefinitionList } from "@/components/ProcessDefinitionList";
import { RequireAuth } from "@/components/RequireAuth";

type Tab = "processes" | "dmn";

// Zwei Übersichtslisten seit P14-S4 (Prozessdefinitionen/DMN-1.3-
// Entscheidungstabellen) - einfacher Tab-Umschalter statt zweier
// eigenständiger Routen, da beide dieselbe Rolle ("Konfigurationsobjekt der
// Workflow-Engine") einnehmen und nur eine der beiden Listen gleichzeitig
// gebraucht wird.
function HomePageInner() {
  const { t } = useI18n();
  const [tab, setTab] = useState<Tab>("processes");

  return (
    <>
      <div className="tab-bar">
        <button
          type="button"
          className={tab === "processes" ? "tab-button active" : "tab-button"}
          onClick={() => setTab("processes")}
        >
          {t("nav.processesTab")}
        </button>
        <button
          type="button"
          className={tab === "dmn" ? "tab-button active" : "tab-button"}
          onClick={() => setTab("dmn")}
        >
          {t("nav.dmnTab")}
        </button>
      </div>
      {tab === "processes" ? <ProcessDefinitionList /> : <DmnDefinitionList />}
    </>
  );
}

export default function HomePage() {
  return (
    <RequireAuth>
      <HomePageInner />
    </RequireAuth>
  );
}
