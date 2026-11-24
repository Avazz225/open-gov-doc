import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminSidebar } from "@/components/AdminSidebar";
import { I18nProvider } from "@/i18n";
import { invalidateLicenseHints } from "@/lib/license-hints";

// Muted "Mit Lizenzerweiterung" sidebar group (P91-S6).

vi.mock("next/navigation", () => ({ usePathname: () => "/users/" }));

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

const getLicenseComponentsMock = vi.fn();
const getLicenseDisplayConfigMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getLicenseComponents: (...args: unknown[]) => getLicenseComponentsMock(...args),
  getLicenseDisplayConfig: (...args: unknown[]) => getLicenseDisplayConfigMock(...args),
}));

function renderSidebar() {
  return render(
    <I18nProvider>
      <AdminSidebar />
    </I18nProvider>
  );
}

describe("licence upgrade hints in the sidebar", () => {
  beforeEach(() => {
    invalidateLicenseHints();
    window.localStorage.clear();
    mockPermissions = ["admin.user_management"];
    getLicenseComponentsMock.mockReset().mockResolvedValue([
      { service_type: "webdav-connector", policy: "demo", status: "demo" },
      { service_type: "workflow-service", policy: "demo", status: "licensed" },
    ]);
    getLicenseDisplayConfigMock.mockReset().mockResolvedValue({
      show_upgrade_hints: true,
      updated_at: "2026-11-24T00:00:00Z",
    });
  });

  it("lists uncovered components as plain text for non-licence admins", async () => {
    renderSidebar();

    const entry = await screen.findByText("Office-Direktbearbeitung & Netzlaufwerk");
    expect(entry.closest("a")).toBeNull();
    expect(screen.getByText("Mit Lizenzerweiterung")).toBeInTheDocument();
    expect(screen.queryByText("Prozesse & Umlaufmappen")).not.toBeInTheDocument();
  });

  it("links to the licence page's extension section for licence admins", async () => {
    mockPermissions = ["admin.license"];
    renderSidebar();

    const entry = await screen.findByText("Office-Direktbearbeitung & Netzlaufwerk");
    expect(entry.closest("a")).toHaveAttribute("href", expect.stringMatching(/^\/license\/?#erweiterung$/));
  });

  it("hides the group when the installation switched hints off, and reacts to the switch", async () => {
    getLicenseDisplayConfigMock.mockResolvedValue({ show_upgrade_hints: false, updated_at: "x" });
    renderSidebar();
    await act(async () => {});
    expect(screen.queryByTestId("license-upgrade-hints")).not.toBeInTheDocument();

    getLicenseDisplayConfigMock.mockResolvedValue({ show_upgrade_hints: true, updated_at: "y" });
    await act(async () => invalidateLicenseHints());
    expect(await screen.findByTestId("license-upgrade-hints")).toBeInTheDocument();
  });

  it("shows nothing when the lookup fails", async () => {
    getLicenseComponentsMock.mockRejectedValue(new Error("down"));
    renderSidebar();
    await act(async () => {});
    expect(screen.queryByTestId("license-upgrade-hints")).not.toBeInTheDocument();
  });
});
