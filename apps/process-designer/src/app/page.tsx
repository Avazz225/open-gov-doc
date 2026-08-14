"use client";

import { useState } from "react";
import { useI18n } from "@/i18n";
import { DmnDefinitionList } from "@/components/DmnDefinitionList";
import { ProcessDefinitionList } from "@/components/ProcessDefinitionList";
import { RequireAuth } from "@/components/RequireAuth";

type Tab = "processes" | "dmn";

// Two overview lists since P14-S4 (process definitions/DMN 1.3
// decision tables) - a simple tab switcher instead of two
// separate routes, since both play the same role ("configuration object of
// the workflow engine") and only one of the two lists is
// needed at a time.
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
