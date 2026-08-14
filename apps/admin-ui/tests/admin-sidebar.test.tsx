import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSidebar } from "@/components/AdminSidebar";
import { I18nProvider } from "@/i18n";

vi.mock("next/navigation", () => ({
  usePathname: () => "/users/",
}));

let mockPermissions: string[] = [];

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

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
    mockPermissions = ["admin.user_management"];
  });

  it("shows both groups expanded by default with their nav items", () => {
    renderSidebar();

    expect(screen.getByText("Nutzer & Rollen")).toBeInTheDocument();
    expect(screen.getByText("Objekttypen")).toBeInTheDocument();
    expect(screen.getByText("Registry")).toBeInTheDocument();
    expect(screen.getByText("Installationsverwaltung")).toBeInTheDocument();
    expect(screen.getByText("Superuser Break-Glass")).toBeInTheDocument();
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

  it("hides the users entry without the domain-admin-users capability (4.6)", () => {
    mockPermissions = [];

    renderSidebar();

    expect(screen.queryByText("Nutzer & Rollen")).not.toBeInTheDocument();
    expect(screen.getByText("Objekttypen")).toBeInTheDocument();
  });

  it("hides the query console entry without the admin.query_console capability (6.1, P8-S1)", () => {
    mockPermissions = [];

    renderSidebar();

    expect(screen.queryByText("Query-Konsole")).not.toBeInTheDocument();
  });

  it("shows the query console entry with the admin.query_console capability", () => {
    mockPermissions = ["admin.query_console"];

    renderSidebar();

    expect(screen.getByText("Query-Konsole")).toBeInTheDocument();
  });

  it("hides the teamspaces entry without the admin.teamspace_management capability (Post-Roadmap Phase 22 Session 5)", () => {
    mockPermissions = [];

    renderSidebar();

    expect(screen.queryByText("Team-Arbeitsbereiche")).not.toBeInTheDocument();
  });

  it("shows the teamspaces entry with the admin.teamspace_management capability", () => {
    mockPermissions = ["admin.teamspace_management"];

    renderSidebar();

    expect(screen.getByText("Team-Arbeitsbereiche")).toBeInTheDocument();
  });
});
