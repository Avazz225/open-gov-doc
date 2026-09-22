import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DashboardWidgets } from "@/components/DashboardWidgets";
import { I18nProvider } from "@/i18n";

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

function renderWidgets() {
  return render(
    <I18nProvider>
      <DashboardWidgets />
    </I18nProvider>
  );
}

// Role-dependent dashboard (Concept 8, P69-S2, ADR 0201) - reuses
// `AdminSidebar`'s own `GROUPS`/`visibleItems`, so these tests mirror
// `admin-sidebar.test.tsx`'s capability-gating tests at the widget level.
describe("DashboardWidgets", () => {
  beforeEach(() => {
    mockPermissions = ["admin.user_management", "admin.object_config", "breakglass.approve"];
  });

  it("renders one widget per visible group, with its visible items as links", () => {
    renderWidgets();

    expect(screen.getByText("Verwaltung")).toBeInTheDocument();
    expect(screen.getByText("Nutzende & Rollen")).toBeInTheDocument();
    expect(screen.getByText("Objekttypen")).toBeInTheDocument();
    expect(screen.getByText("Sicherheit")).toBeInTheDocument();
    expect(screen.getByText("Superuser Break-Glass")).toBeInTheDocument();
  });

  it("omits a group entirely once none of its items are visible", () => {
    mockPermissions = [];

    renderWidgets();

    expect(screen.queryByText("Nutzende & Rollen")).not.toBeInTheDocument();
    // "Registry"/"Installationsverwaltung" carry no `requiresCapability`,
    // so the "Installations" group still renders with at least that item.
    expect(screen.getByText("Installationen")).toBeInTheDocument();
  });
});
