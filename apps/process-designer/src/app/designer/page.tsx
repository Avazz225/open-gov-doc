"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createProcessDefinition,
  getProcessDefinition,
  listFederationInstallations,
} from "@/lib/api";
import type { FederationInstallationSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { RequireAuth } from "@/components/RequireAuth";
import type { BpmnDesignerHandle } from "@/components/BpmnDesigner";

const CAN_MANAGE_CAPABILITY = "admin.object_config";

// Minimales Start-Event-Diagramm als Vorlage für "Neu erstellen" - `bpmn-js`
// braucht immer ein gültiges, importierbares Ausgangsdiagramm.
const STARTER_BPMN_XML = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_1" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_1">
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1">
        <dc:Bounds x="173" y="102" width="36" height="36" />
      </bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

// `next/dynamic`/`{ssr:false}`, da bpmn-js beim Modul-Import direkt das DOM
// anfasst (eigenes SVG-Canvas) - unvereinbar mit Next.js' Build-Zeit-
// Renderdurchlauf unter `output:"export"`.
const BpmnDesigner = dynamic(
  () => import("@/components/BpmnDesigner").then((m) => m.BpmnDesigner),
  { ssr: false }
);

function DesignerPageInner() {
  const { accessToken, permissions } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = searchParams.get("id");
  const canManage = permissions.includes(CAN_MANAGE_CAPABILITY);

  const [name, setName] = useState("");
  const [initialXml, setInitialXml] = useState<string | null>(null);
  // `null` = noch nicht geladen - Rendern von BpmnDesigner wartet darauf, damit
  // die Föderations-Properties-Panel-Gruppe (7.4, P6-S9) beim allerersten
  // Aufbau des Modelers bereits die richtige Installationsliste injiziert
  // bekommt (siehe components/BpmnDesigner.tsx).
  const [federationInstallations, setFederationInstallations] = useState<
    FederationInstallationSummary[] | null
  >(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const handleRef = useRef<BpmnDesignerHandle | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const loadedForId = useRef<string | null>(null);

  if (loadedForId.current !== id) {
    loadedForId.current = id;
    if (id && accessToken) {
      getProcessDefinition(accessToken, Number(id))
        .then((definition) => {
          setName(definition.name);
          setInitialXml(definition.bpmn_xml);
        })
        .catch(() => setLoadError(t("designer.loadError")));
    } else {
      setName("");
      setInitialXml(STARTER_BPMN_XML);
    }
  }

  useEffect(() => {
    if (!accessToken) return;
    listFederationInstallations(accessToken)
      .then(setFederationInstallations)
      // Fail-open statt den ganzen Designer zu blockieren - ohne erreichbaren
      // Hub bleibt die Föderations-Gruppe einfach ausgeblendet.
      .catch(() => setFederationInstallations([]));
  }, [accessToken]);

  const handleReady = useCallback((handle: BpmnDesignerHandle) => {
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
      setLoadError(err instanceof Error ? err.message : t("designer.importError"));
    }
  }

  async function handleExport() {
    if (!handleRef.current) return;
    const xml = await handleRef.current.exportXml();
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name || "process"}.bpmn`;
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
      const saved = await createProcessDefinition(accessToken, {
        name: name.trim(),
        bpmnXml: xml,
      });
      setSaveSuccess(t("designer.saveSuccess", { version: saved.version }));
      router.replace(`/designer/?id=${saved.id}`);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : t("designer.saveError"));
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
        <span className="hint">{t("designer.nameHint")}</span>
        <input
          ref={fileInputRef}
          type="file"
          accept=".bpmn,.xml"
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
      {initialXml !== null && federationInstallations !== null && (
        <BpmnDesigner
          initialXml={initialXml}
          federationInstallations={federationInstallations}
          onReady={handleReady}
          onImportError={(message) => setLoadError(message)}
        />
      )}
    </div>
  );
}

// `useSearchParams()` braucht laut Next.js unter `output:"export"` eine
// umschließende Suspense-Grenze, sonst schlägt der statische Build fehl.
export default function DesignerPage() {
  return (
    <RequireAuth>
      <Suspense fallback={null}>
        <DesignerPageInner />
      </Suspense>
    </RequireAuth>
  );
}
