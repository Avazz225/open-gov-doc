"use client";

import BpmnModeler from "bpmn-js/lib/Modeler";
import {
  BpmnPropertiesPanelModule,
  BpmnPropertiesProviderModule,
  CamundaPlatformPropertiesProviderModule,
} from "bpmn-js-properties-panel";
import camundaModdleDescriptor from "camunda-bpmn-moddle/resources/camunda.json";
import { useEffect, useRef, useState } from "react";
import { SlidersHorizontal, X } from "lucide-react";
import { useI18n } from "@/i18n";
import type { FederationInstallation } from "./FederatedStepPropertiesProvider";
import { FederatedStepPropertiesProviderModule } from "./FederatedStepPropertiesProvider";
import { SignatureTaskPropertiesProviderModule } from "./SignatureTaskPropertiesProvider";
import { DmnValidationPropertiesProviderModule } from "./DmnValidationPropertiesProvider";

import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "@bpmn-io/properties-panel/assets/properties-panel.css";

export interface BpmnDesignerHandle {
  exportXml: () => Promise<string>;
  importXml: (xml: string) => Promise<void>;
}

interface BpmnDesignerProps {
  initialXml: string;
  // Injected statically when the modeler is created (7.4, P6-S9) - loaded
  // once by the calling `designer/page.tsx` before mounting (see
  // `lib/api.ts#listFederationInstallations`). Empty if no federation hub
  // is configured - `FederatedStepPropertiesProvider` then hides the
  // entire group.
  federationInstallations: FederationInstallation[];
  // Injected statically the same way (7.1, P71-S3) - every currently
  // loaded DMN family's `decision_id` (see
  // `lib/api.ts#listDmnDefinitions`), used by
  // `DmnValidationPropertiesProvider` to warn when a `businessRuleTask`'s
  // `camunda:decisionRef` doesn't match any of them.
  knownDecisionIds: string[];
  onImportError?: (message: string) => void;
  // Callback prop instead of `forwardRef`/`useImperativeHandle` - avoids any
  // uncertainty about ref forwarding through `next/dynamic` (which this
  // component is loaded with, `{ssr:false}`, see designer/page.tsx) and
  // is just as expressive for the case needed here (the parent
  // component only needs `exportXml`/`importXml` after the modeler's
  // initialization).
  onReady?: (handle: BpmnDesignerHandle) => void;
}

// Manual `useRef`/`useEffect` mounting instead of a React wrapper - no
// actively maintained React wrapper for bpmn-js is available (`react-bpmn`
// stalled since 2020, see ADR 0026); bpmn-js manipulates the DOM directly
// anyway (its own SVG canvas), which isn't React-idiomatic. Loaded via
// `next/dynamic`/`{ssr:false}` by the calling code (direct
// DOM access on module import is incompatible with Next.js'
// build-time render pass under `output:"export"`).
export function BpmnDesigner({
  initialXml,
  federationInstallations,
  knownDecisionIds,
  onImportError,
  onReady,
}: BpmnDesignerProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const propertiesPanelRef = useRef<HTMLDivElement>(null);
  const { t } = useI18n();

  useEffect(() => {
    if (!canvasRef.current || !propertiesPanelRef.current) return;

    const modeler = new BpmnModeler({
      container: canvasRef.current,
      propertiesPanel: { parent: propertiesPanelRef.current },
      additionalModules: [
        BpmnPropertiesPanelModule,
        BpmnPropertiesProviderModule,
        CamundaPlatformPropertiesProviderModule,
        SignatureTaskPropertiesProviderModule,
        FederatedStepPropertiesProviderModule,
        DmnValidationPropertiesProviderModule,
        // Not a dedicated bpmn-js module, but a didi inline binding for the
        // static installation list (see FederatedStepPropertiesProvider.tsx).
        { federationInstallations: ["value", federationInstallations] },
        { knownDecisionIds: ["value", knownDecisionIds] },
      ],
      moddleExtensions: { camunda: camundaModdleDescriptor },
    });

    onReady?.({
      exportXml: async () => {
        const { xml } = await modeler.saveXML({ format: true });
        return xml ?? "";
      },
      importXml: async (xml: string) => {
        await modeler.importXML(xml);
      },
    });

    modeler.importXML(initialXml).catch((err: unknown) => {
      onImportError?.(err instanceof Error ? err.message : String(err));
    });

    return () => {
      modeler.destroy();
    };
    // Only on the first mount - a later change to `initialXml` goes
    // through `importXml()` on the `onReady` handle (toolbar "Import file"),
    // no re-mounting of the entire canvas is needed/wanted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Below 768px the 300px properties panel left the canvas ~70px (P83-S10,
  // ADR 0238 Decision 6). There it is hidden behind a toggle and opens as a
  // sheet over the canvas's right edge; from 768px it is the fixed column it
  // always was. The panel element stays mounted either way (only CSS
  // changes), because bpmn-js-properties-panel renders into it.
  const [propertiesOpen, setPropertiesOpen] = useState(false);

  return (
    <div className="relative flex min-h-0 flex-1">
      <div className="min-w-0 flex-1" ref={canvasRef} />
      <button
        type="button"
        aria-expanded={propertiesOpen}
        aria-label={propertiesOpen ? t("designer.hideProperties") : t("designer.showProperties")}
        onClick={() => setPropertiesOpen((value) => !value)}
        className="absolute right-3 top-3 z-20 flex min-h-10 min-w-10 items-center justify-center rounded-md border border-border bg-bg text-fg shadow-sm transition-colors hover:bg-hover-bg md:hidden"
      >
        {propertiesOpen ? (
          <X size={18} strokeWidth={1.75} aria-hidden="true" />
        ) : (
          <SlidersHorizontal size={18} strokeWidth={1.75} aria-hidden="true" />
        )}
      </button>
      <div
        className={`overflow-y-auto border-l border-border bg-bg max-md:absolute max-md:inset-y-0 max-md:right-0 max-md:z-10 max-md:w-[min(300px,85%)] max-md:shadow-lg md:w-[300px] md:flex-shrink-0 ${
          propertiesOpen ? "" : "max-md:hidden"
        }`}
        ref={propertiesPanelRef}
      />
    </div>
  );
}
