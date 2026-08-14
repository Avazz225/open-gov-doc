import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamspacesAdmin } from "@/components/TeamspacesAdmin";
import { I18nProvider } from "@/i18n";

function renderTeamspacesAdmin() {
  return render(
    <I18nProvider>
      <TeamspacesAdmin />
    </I18nProvider>
  );
}

const listAllTeamspacesMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listAllTeamspaces: (...args: unknown[]) => listAllTeamspacesMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "admin", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("TeamspacesAdmin", () => {
  beforeEach(() => {
    listAllTeamspacesMock.mockReset();
  });

  it("lists every teamspace regardless of the caller's own membership", async () => {
    listAllTeamspacesMock.mockResolvedValue([
      {
        id: "ts1",
        name: "Projekt X",
        description: "Testbeschreibung",
        root_folder_id: "f1",
        created_by: "alice",
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
        member_count: 3,
      },
    ]);

    renderTeamspacesAdmin();

    expect(await screen.findByText("Projekt X")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows an empty state without any teamspaces", async () => {
    listAllTeamspacesMock.mockResolvedValue([]);

    renderTeamspacesAdmin();

    expect(await screen.findByText("Noch keine Teamspaces.")).toBeInTheDocument();
  });

  it("shows an unreachable state when teamspace-service is not reachable", async () => {
    listAllTeamspacesMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderTeamspacesAdmin();

    expect(await screen.findByText(/Teamspace Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("shows the backend error message when access is forbidden", async () => {
    const { ApiError } = await import("@/lib/api");
    listAllTeamspacesMock.mockRejectedValue(new ApiError(403, "admin.teamspace_management erforderlich"));

    renderTeamspacesAdmin();

    expect(await screen.findByText("admin.teamspace_management erforderlich")).toBeInTheDocument();
  });
});
