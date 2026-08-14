"use client";

import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

// Generic drag handle for resizable panels (user feedback after P4-S3,
// 3-way split explorer layout, 8). Deliberately without an external layout
// library - computes the new size on every "pointermove" relative to
// `containerRef`'s bounding box, instead of accumulating deltas (more
// robust against lost events).
export function Splitter({
  orientation,
  containerRef,
  onResize,
  label,
}: {
  orientation: "vertical" | "horizontal";
  containerRef: RefObject<HTMLElement | null>;
  onResize: (offsetPx: number) => void;
  label: string;
}) {
  function handlePointerDown(event: ReactPointerEvent) {
    event.preventDefault();

    function handleMove(moveEvent: PointerEvent) {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return;
      const offset =
        orientation === "vertical" ? moveEvent.clientX - rect.left : moveEvent.clientY - rect.top;
      onResize(offset);
    }

    function handleUp() {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
  }

  return (
    <div
      className={`splitter splitter-${orientation}`}
      onPointerDown={handlePointerDown}
      role="separator"
      aria-orientation={orientation}
      aria-label={label}
      tabIndex={0}
    />
  );
}
