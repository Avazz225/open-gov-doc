"use client";

import { useId, type ReactNode } from "react";
import type { LayoutData, LayoutField } from "@/lib/api";

// Shared renderer for the row/column grid of a form layout (2.2b) - used
// alike by MetadataPanel (display), SearchPane (search), and UploadForm
// (upload) so the breakpoint logic and grid markup aren't duplicated three
// times.
//
// Responsive behavior: below the width stored in the layout, a multi-column
// layout is rendered single-column regardless of its configuration. Since
// P23-S6 this is measured via a real CSS container query
// (`container-type: inline-size` + `@container`) instead of a
// `window.innerWidth` resize listener - so it's now this panel's actual
// width that counts, not the window width anymore (previously a narrowly
// resized panel in a wide window incorrectly stayed multi-column due to a
// bug, see docs/services/user-ui.md "Open Points", now fixed).
// The threshold itself is admin-configurable per object type
// (`layout.responsive_breakpoint_px`) - since the width condition of an
// `@container` rule cannot reference a CSS variable (only style queries can
// do that, but they don't cover width), the rule is generated as an inline
// `<style>` block with the concrete pixel value, scoped via a
// per-component-instance unique `data-` identifier (`useId()`) - this way
// multiple simultaneously open panels with different layouts (e.g. two
// document tabs of different object types) don't interfere with each
// other.
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
