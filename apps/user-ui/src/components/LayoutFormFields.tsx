"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { LayoutData, LayoutField } from "@/lib/api";

// Responsives Verhalten (2.2b): unterhalb der im Layout hinterlegten
// Fensterbreite wird ein mehrspaltiges Layout unabhängig von seiner
// Konfiguration immer einspaltig dargestellt. Bewusst an der Fensterbreite
// gemessen (nicht an der tatsächlichen Breite dieses - über den `Splitter`
// frei verkleinerbaren - Panels), da das Konzept explizit "Fensterbreite"
// verlangt; ein schmal gezogenes Panel in einem breiten Fenster bleibt daher
// mehrspaltig, siehe docs/services/user-ui.md "Offene Punkte".
function useIsBelowBreakpoint(breakpointPx: number): boolean {
  const [isBelow, setIsBelow] = useState(
    () => typeof window !== "undefined" && window.innerWidth < breakpointPx
  );

  useEffect(() => {
    function update() {
      setIsBelow(window.innerWidth < breakpointPx);
    }
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [breakpointPx]);

  return isBelow;
}

// Gemeinsamer Renderer für das Zeilen/Spalten-Grid eines Formular-Layouts
// (2.2b) - von MetadataPanel (Anzeige), SearchPane (Suche) und UploadForm
// (Upload) gleichermaßen genutzt, damit die Breakpoint-Logik und das
// Grid-Markup nicht drei Mal dupliziert werden.
export function LayoutFormFields({
  layout,
  renderField,
}: {
  layout: LayoutData;
  renderField: (field: LayoutField) => ReactNode;
}) {
  const stacked = useIsBelowBreakpoint(layout.responsive_breakpoint_px);

  return (
    <div className="layout-grid">
      {layout.rows.map((row, rowIndex) => (
        <div className="layout-grid-row" key={rowIndex}>
          {row.columns.map((field) => (
            <div
              className="layout-grid-field"
              key={field.attribute}
              style={{ flexBasis: stacked ? "100%" : undefined }}
            >
              {renderField(field)}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
