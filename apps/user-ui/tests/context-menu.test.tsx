import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ContextMenu } from "@/components/ContextMenu";

describe("ContextMenu", () => {
  it("renders items at the given position and calls onSelect + onClose when clicked", () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <ContextMenu
        x={10}
        y={20}
        items={[{ label: "Löschen", onSelect }]}
        onClose={onClose}
      />
    );

    fireEvent.click(screen.getByText("Löschen"));

    expect(onSelect).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("does not call onSelect for a disabled item", () => {
    const onSelect = vi.fn();
    render(
      <ContextMenu
        x={0}
        y={0}
        items={[{ label: "Löschen", onSelect, disabled: true }]}
        onClose={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText("Löschen"));

    expect(onSelect).not.toHaveBeenCalled();
  });

  it("closes when clicking outside the menu", () => {
    const onClose = vi.fn();
    render(
      <div>
        <button type="button">Outside</button>
        <ContextMenu x={0} y={0} items={[{ label: "Löschen", onSelect: vi.fn() }]} onClose={onClose} />
      </div>
    );

    fireEvent.mouseDown(screen.getByText("Outside"));

    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<ContextMenu x={0} y={0} items={[{ label: "Löschen", onSelect: vi.fn() }]} onClose={onClose} />);

    fireEvent.keyDown(document, { key: "Escape" });

    expect(onClose).toHaveBeenCalled();
  });
});
