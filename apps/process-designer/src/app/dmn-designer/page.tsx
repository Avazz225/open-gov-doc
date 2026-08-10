"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import { ApiError, createDmnDefinition, getDmnDefinition } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { RequireAuth } from "@/components/RequireAuth";
import type { DmnDesignerHandle } from "@/components/DmnDesigner";

const CAN_MANAGE_CAPABILITY = "admin.object_config";

// Minimale Start-Entscheidungstabelle als Vorlage für "Neu erstellen" -
// dmn-js braucht wie bpmn-js immer ein gültiges, importierbares
// Ausgangsdiagramm (7.1, P14-S4).
const STARTER_DMN_XML = `<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="Definitions_1" name="definitions" namespace="http://camunda.org/schema/1.0/dmn">
  <decision id="Decision_1" name="Neue Entscheidungstabelle">
    <decisionTable id="DecisionTable_1" hitPolicy="UNIQUE">
      <input id="Input_1">
        <inputExpression id="InputExpression_1" typeRef="string">
          <text></text>
        </inputExpression>
      </input>
      <output id="Output_1" typeRef="string" />
    </decisionTable>
  </decision>
</definitions>`;

// `next/dynamic`/`{ssr:false}` - siehe designer/page.tsx (dmn-js manipuliert
// wie bpmn-js beim Modul-Import direkt das DOM).
const DmnDesigner = dynamic(
  () => import("@/components/DmnDesigner").then((m) => m.DmnDesigner),
  { ssr: false }
);

function DmnDesignerPageInner() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = searchParams.get("id");
  const canManage = permissions.includes(CAN_MANAGE_CAPABILITY);

  const [name, setName] = useState("");
  const [initialXml, setInitialXml] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const handleRef = useRef<DmnDesignerHandle | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // `undefined`-Sentinel statt `null` - siehe designer/page.tsx (P14-S4-Fund):
  // `searchParams.get("id")` liefert bei fehlendem `?id=` ebenfalls `null`,
  // ein mit `null` initialisierter Ref würde die Erstinitialisierung sonst
  // fälschlich überspringen.
  const loadedForId = useRef<string | null | undefined>(undefined);

  if (loadedForId.current !== id) {
    loadedForId.current = id;
    if (id && accessToken) {
      getDmnDefinition(accessToken, Number(id))
        .then((definition) => {
          setName(definition.name);
          setInitialXml(definition.dmn_xml);
        })
        .catch(() => setLoadError(t("dmnDesigner.loadError")));
    } else {
      setName("");
      setInitialXml(STARTER_DMN_XML);
    }
  }

  const handleReady = useCallback((handle: DmnDesignerHandle) => {
    handleRef.current = handle;
  }, []);

  async function handleImportFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !handleRef.current) return;
    try {
      const text = await file.text();
      await handleRef.current.importXml(text);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : t("dmnDesigner.importError"));
    }
  }

  async function handleExport() {
    if (!handleRef.current) return;
    const xml = await handleRef.current.exportXml();
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name || "decision"}.dmn`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async function handleSave() {
    if (!accessToken || !handleRef.current || !name.trim()) return;
    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(null);
    try {
      const xml = await handleRef.current.exportXml();
      const saved = await createDmnDefinition(accessToken, { name: name.trim(), dmnXml: xml });
      setSaveSuccess(t("dmnDesigner.saveSuccess", { version: saved.version }));
      router.replace(`/dmn-designer/?id=${saved.id}`);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : t("dmnDesigner.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="designer-shell">
      <div className="designer-toolbar">
        <Link href="/">
          <button type="button">{t("common.back")}</button>
        </Link>
        <label>
          {t("designer.nameLabel")}
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={!canManage}
          />
        </label>
        <span className="hint">{t("dmnDesigner.nameHint")}</span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".dmn,.xml"
          hidden
          onChange={handleImportFile}
        />
        <button type="button" onClick={() => fileInputRef.current?.click()}>
          {t("designer.importButton")}
        </button>
        <button type="button" onClick={handleExport}>
          {t("designer.exportButton")}
        </button>
        {canManage && (
          <button type="button" onClick={handleSave} disabled={isSaving || !name.trim()}>
            {isSaving ? t("designer.saving") : t("designer.saveButton")}
          </button>
        )}
        {saveSuccess && <span className="success-text">{saveSuccess}</span>}
        {saveError && (
          <span className="error-text" role="alert">
            {saveError}
          </span>
        )}
        {loadError && (
          <span className="error-text" role="alert">
            {loadError}
          </span>
        )}
      </div>
      {initialXml !== null && (
        <DmnDesigner
          initialXml={initialXml}
          onReady={handleReady}
          onImportError={(message) => setLoadError(message)}
        />
      )}
    </div>
  );
}

// `useSearchParams()` braucht laut Next.js unter `output:"export"` eine
// umschließende Suspense-Grenze, siehe designer/page.tsx.
export default function DmnDesignerPage() {
  return (
    <RequireAuth>
      <Suspense fallback={null}>
        <DmnDesignerPageInner />
      </Suspense>
    </RequireAuth>
  );
}
