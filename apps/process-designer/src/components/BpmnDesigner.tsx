"use client";

import BpmnModeler from "bpmn-js/lib/Modeler";
import {
  BpmnPropertiesPanelModule,
  BpmnPropertiesProviderModule,
  CamundaPlatformPropertiesProviderModule,
} from "bpmn-js-properties-panel";
import camundaModdleDescriptor from "camunda-bpmn-moddle/resources/camunda.json";
import { useEffect, useRef } from "react";
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

  return (
    <div className="flex min-h-0 flex-1">
      <div className="min-w-0 flex-1" ref={canvasRef} />
      <div
        className="w-[300px] flex-shrink-0 overflow-y-auto border-l border-border"
        ref={propertiesPanelRef}
      />
    </div>
  );
}
