import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSidebar } from "@/components/AdminSidebar";
import { I18nProvider } from "@/i18n";

vi.mock("next/navigation", () => ({
  usePathname: () => "/users/",
}));

function renderSidebar() {
  return render(
    <I18nProvider>
      <AdminSidebar />
    </I18nProvider>
  );
}

describe("AdminSidebar", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows both groups expanded by default with their nav items", () => {
    renderSidebar();

    expect(screen.getByText("Nutzer & Rollen")).toBeInTheDocument();
    expect(screen.getByText("Objekttypen")).toBeInTheDocument();
    expect(screen.getByText("Registry")).toBeInTheDocument();
    expect(screen.getByText("Installationsverwaltung")).toBeInTheDocument();
  });

  it("collapses a group and persists the state across remounts", () => {
    const { unmount } = renderSidebar();

    fireEvent.click(screen.getByText("Verwaltung"));

    expect(screen.queryByText("Nutzer & Rollen")).not.toBeInTheDocument();
    unmount();

    renderSidebar();
    expect(screen.queryByText("Nutzer & Rollen")).not.toBeInTheDocument();
    expect(screen.getByText("Installationsverwaltung")).toBeInTheDocument();
  });
});
