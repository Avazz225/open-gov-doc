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
  // Callback pattern instead of `forwardRef`, identical to `BpmnDesigner.tsx`
  // (see the reasoning there: no actively maintained React wrapper for the
  // bpmn.io toolkits, no risk from ref forwarding through
  // `next/dynamic`).
  onReady?: (handle: DmnDesignerHandle) => void;
}

// Same manual `useRef`/`useEffect` mounting as `BpmnDesigner.tsx` -
// `dmn-js`, like `bpmn-js`, manipulates the DOM directly (its own SVG canvas for
// the decision table/DRD view), not React-idiomatic. Also loaded
// via `next/dynamic`/`{ssr:false}` (see dmn-designer/page.tsx).
// Compatibility with the pinned `bpmn-js` 18.22.1 stack empirically
// verified via spike (P14-S4): `dmn-js` 17.10.1 uses the same
// `diagram-js` ^15.23.2 major version as `bpmn-js`; a real
// `next build`/static export run as well as a live rendering test
// (decision table view including hit policy dropdown, rule rows) both
// ran without errors - no fallback to a raw XML editor needed.
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
    // Only on first mount - see BpmnDesigner.tsx for the same reasoning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div className="designer-canvas dmn-canvas" ref={containerRef} />;
}
