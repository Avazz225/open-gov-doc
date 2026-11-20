import { useRef, useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NavDrawer } from "@/components/NavDrawer";

// The sidebar drawer below 768px (P83-S12, ADR 0238 Decision 3) behaves as a
// modal dialog: focus moves in and is trapped, Escape and the backdrop close
// it, the page is scroll-locked while open, focus returns to the opener.
function Harness() {
  const [open, setOpen] = useState(false);
  const openerRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={openerRef} type="button" onClick={() => setOpen(true)}>
        open
      </button>
      <NavDrawer
        open={open}
        onClose={() => setOpen(false)}
        label="Navigation"
        closeLabel="Navigation schließen"
        returnFocusRef={openerRef}
      >
        <a href="/a">first link</a>
        <a href="/b">last link</a>
      </NavDrawer>
    </>
  );
}

describe("NavDrawer", () => {
  it("opens as a modal dialog with focus on its first control and a locked page", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "open" }));
    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "Navigation schließen" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("traps Tab inside the drawer in both directions", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "open" }));
    const close = screen.getByRole("button", { name: "Navigation schließen" });
    const last = screen.getByRole("link", { name: "last link" });
    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  it("closes on Escape, unlocks the page and returns focus to the opener", () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "open" });
    fireEvent.click(opener);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(document.body.style.overflow).toBe("");
    expect(opener).toHaveFocus();
  });

  it("closes on a backdrop click", () => {
    const { container } = render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "open" }));
    fireEvent.click(container.ownerDocument.querySelector(".bg-black\\/40") as HTMLElement);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
