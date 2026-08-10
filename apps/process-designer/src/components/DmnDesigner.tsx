"use client";

import DmnModeler from "dmn-js/lib/Modeler";
import { useEffect, useRef } from "react";

import "dmn-js/dist/assets/diagram-js.css";
import "dmn-js/dist/assets/dmn-js-shared.css";
import "dmn-js/dist/assets/dmn-js-drd.css";
import "dmn-js/dist/assets/dmn-js-decision-table.css";
import "dmn-js/dist/assets/dmn-js-decision-table-controls.css";
import "dmn-js/dist/assets/dmn-js-literal-expression.css";
import "dmn-js/dist/assets/dmn-font/css/dmn.css";

export interface DmnDesignerHandle {
  exportXml: () => Promise<string>;
  importXml: (xml: string) => Promise<void>;
}

interface DmnDesignerProps {
  initialXml: string;
  onImportError?: (message: string) => void;
  // Callback- statt `forwardRef`-Muster, identisch zu `BpmnDesigner.tsx`
  // (siehe dortige Begründung: kein aktiv gepflegter React-Wrapper für die
  // bpmn.io-Toolkits, kein Risiko durch Ref-Weiterreichung durch
  // `next/dynamic`).
  onReady?: (handle: DmnDesignerHandle) => void;
}

// Gleiches manuelles `useRef`/`useEffect`-Mounting wie `BpmnDesigner.tsx` -
// `dmn-js` manipuliert wie `bpmn-js` direkt das DOM (eigenes SVG-Canvas für
// die Decision-Table-/DRD-Ansicht), nicht React-idiomatisch. Wird ebenfalls
// per `next/dynamic`/`{ssr:false}` eingebunden (siehe dmn-designer/page.tsx).
// Kompatibilität mit der gepinnten `bpmn-js` 18.22.1-Stack per Spike
// empirisch verifiziert (P14-S4): `dmn-js` 17.10.1 nutzt dieselbe
// `diagram-js` ^15.23.2-Major-Version wie `bpmn-js`, ein realer
// `next build`/Static-Export-Durchlauf sowie ein Live-Rendering-Test
// (Decision-Table-Ansicht inkl. Hit-Policy-Dropdown, Regel-Zeilen) liefen
// beide ohne Fehler - kein Fallback auf einen rohen XML-Editor nötig.
export function DmnDesigner({ initialXml, onImportError, onReady }: DmnDesignerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const modeler = new DmnModeler({ container: containerRef.current });

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
    // Nur beim ersten Mounten - siehe BpmnDesigner.tsx für dieselbe Begründung.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div className="designer-canvas dmn-canvas" ref={containerRef} />;
}
