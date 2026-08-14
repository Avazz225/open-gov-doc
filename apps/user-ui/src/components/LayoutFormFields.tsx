"use client";

import { useId, type ReactNode } from "react";
import type { LayoutData, LayoutField } from "@/lib/api";

// Gemeinsamer Renderer für das Zeilen/Spalten-Grid eines Formular-Layouts
// (2.2b) - von MetadataPanel (Anzeige), SearchPane (Suche) und UploadForm
// (Upload) gleichermaßen genutzt, damit die Breakpoint-Logik und das
// Grid-Markup nicht drei Mal dupliziert werden.
//
// Responsives Verhalten: unterhalb der im Layout hinterlegten Breite wird ein
// mehrspaltiges Layout unabhängig von seiner Konfiguration einspaltig
// dargestellt. Seit P23-S6 über eine echte CSS-Container-Query
// (`container-type: inline-size` + `@container`) statt eines
// `window.innerWidth`-Resize-Listeners gemessen - dadurch zählt jetzt die
// tatsächliche Breite DIESES Panels, nicht mehr die Fensterbreite (vorher
// blieb ein schmal gezogenes Panel in einem breiten Fenster bugbedingt
// mehrspaltig, siehe docs/services/user-ui.md "Offene Punkte", jetzt behoben).
// Der Schwellwert selbst ist pro Objekttyp admin-konfigurierbar
// (`layout.responsive_breakpoint_px`) - da die Breiten-Bedingung einer
// `@container`-Regel keine CSS-Variable referenzieren kann (nur
// Style-Queries können das, decken aber keine Breite ab), wird die Regel als
// Inline-`<style>`-Block mit dem konkreten Pixelwert erzeugt, gescoped über
// eine pro Komponenteninstanz eindeutige `data-`-Kennung (`useId()`) - so
// beeinflussen sich mehrere gleichzeitig offene Panels mit unterschiedlichen
// Layouts (z. B. zwei Dokument-Tabs verschiedener Objekttypen) nicht
// gegenseitig.
export function LayoutFormFields({
  layout,
  renderField,
}: {
  layout: LayoutData;
  renderField: (field: LayoutField) => ReactNode;
}) {
  const containerId = useId().replace(/[^a-zA-Z0-9]/g, "");

  return (
    <div className="layout-grid-container" data-layout-container={containerId}>
      <style>{`
        @container (max-width: ${layout.responsive_breakpoint_px - 1}px) {
          [data-layout-container="${containerId}"] .layout-grid-field {
            flex-basis: 100%;
          }
        }
      `}</style>
      <div className="layout-grid">
        {layout.rows.map((row, rowIndex) => (
          <div className="layout-grid-row" key={rowIndex}>
            {row.columns.map((field) => (
              <div className="layout-grid-field" key={field.attribute}>
                {renderField(field)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
