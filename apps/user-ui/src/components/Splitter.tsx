"use client";

import type { PointerEvent as ReactPointerEvent, RefObject } from "react";

// Generischer Ziehgriff für resizable Panels (Nutzer-Feedback nach P4-S3,
// 3-geteiltes Explorer-Layout, 8). Bewusst ohne externe Layout-Bibliothek -
// berechnet die neue Größe bei jedem "pointermove" relativ zur
// Bounding-Box von `containerRef`, statt kumulierter Deltas zu addieren
// (robuster gegenüber verlorenen Events).
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
