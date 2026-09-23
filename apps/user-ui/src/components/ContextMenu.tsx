"use client";

import { useEffect, useRef } from "react";

export interface ContextMenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
}

// Generic right-click context menu (5.2, since P7-S1c) - positioned at
// the click point, closes on click outside or Escape. Deliberately built
// as a standalone, reusable component instead of another icon button:
// P7-S1d (favorites/"marking") can directly reuse the same menu
// mechanism with another entry.
export function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="fixed z-[10001] min-w-[12rem] rounded-md border border-border bg-surface p-1 text-surface-fg shadow-sm"
      role="menu"
      style={{ top: y, left: x }}
    >
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className="block w-full cursor-pointer rounded-sm border-0 bg-transparent px-3 py-2 text-left text-inherit enabled:hover:bg-accent enabled:hover:text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
          disabled={item.disabled}
          onClick={() => {
            item.onSelect();
            onClose();
          }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
