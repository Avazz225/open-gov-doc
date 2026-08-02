"use client";

import BpmnModeler from "bpmn-js/lib/Modeler";
import {
  BpmnPropertiesPanelModule,
  BpmnPropertiesProviderModule,
  CamundaPlatformPropertiesProviderModule,
} from "bpmn-js-properties-panel";
import camundaModdleDescriptor from "camunda-bpmn-moddle/resources/camunda.json";
import { useEffect, useRef } from "react";
import { SignatureTaskPropertiesProviderModule } from "./SignatureTaskPropertiesProvider";

import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "@bpmn-io/properties-panel/assets/properties-panel.css";

export interface BpmnDesignerHandle {
  exportXml: () => Promise<string>;
  importXml: (xml: string) => Promise<void>;
}

interface BpmnDesignerProps {
  initialXml: string;
  onImportError?: (message: string) => void;
  // Callback-Prop statt `forwardRef`/`useImperativeHandle` - vermeidet jede
  // Unsicherheit über Ref-Weiterreichung durch `next/dynamic` (mit dem diese
  // Komponente eingebunden wird, `{ssr:false}`, siehe designer/page.tsx) und
  // ist genauso ausdrucksstark für den hier gebrauchten Fall (Eltern-
  // Komponente braucht `exportXml`/`importXml` erst nach der Modeler-
  // Initialisierung).
  onReady?: (handle: BpmnDesignerHandle) => void;
}

// Manuelles `useRef`/`useEffect`-Mounting statt eines React-Wrappers - kein
// aktiv gepflegter React-Wrapper für bpmn-js verfügbar (`react-bpmn` seit
// 2020 stehengeblieben, siehe ADR 0026), bpmn-js manipuliert das DOM ohnehin
// direkt (eigenes SVG-Canvas), nicht React-idiomatisch. Wird per
// `next/dynamic`/`{ssr:false}` vom aufrufenden Code eingebunden (direkte
// DOM-Zugriffe beim Modul-Import vertragen sich nicht mit Next.js'
// Build-Zeit-Renderdurchlauf unter `output:"export"`).
export function BpmnDesigner({ initialXml, onImportError, onReady }: BpmnDesignerProps) {
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
    // Nur beim ersten Mounten - ein späteres Ändern von `initialXml` läuft
    // über `importXml()` am `onReady`-Handle (Toolbar "Datei importieren"),
    // kein Neu-Mounten der gesamten Canvas nötig/gewollt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="designer-body">
      <div className="designer-canvas" ref={canvasRef} />
      <div className="designer-properties-panel" ref={propertiesPanelRef} />
    </div>
  );
}
