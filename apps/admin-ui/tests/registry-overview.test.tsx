import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RegistryOverview } from "@/components/RegistryOverview";
import { I18nProvider } from "@/i18n";

function renderRegistryOverview() {
  return render(
    <I18nProvider>
      <RegistryOverview />
    </I18nProvider>
  );
}

const listServiceInstancesMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listServiceInstances: (...args: unknown[]) => listServiceInstancesMock(...args),
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

describe("RegistryOverview", () => {
  beforeEach(() => {
    listServiceInstancesMock.mockReset();
  });

  it("shows healthy and unhealthy instances with a status badge", async () => {
    listServiceInstancesMock.mockResolvedValue([
      {
        instance_id: "storage-service-abc",
        service_type: "storage-service",
        version: "0.1.0",
        address: "http://storage-service:8000",
        healthy: true,
        registered_at: "2026-01-01T00:00:00Z",
        last_heartbeat_at: "2026-01-01T00:00:00Z",
      },
      {
        instance_id: "folder-service-def",
        service_type: "folder-service",
        version: "0.1.0",
        address: "http://folder-service:8000",
        healthy: false,
        registered_at: "2026-01-01T00:00:00Z",
        last_heartbeat_at: "2026-01-01T00:00:00Z",
      },
    ]);

    renderRegistryOverview();

    expect(await screen.findByText("storage-service")).toBeInTheDocument();
    expect(screen.getByText("healthy")).toBeInTheDocument();
    expect(screen.getByText("down")).toBeInTheDocument();
  });

  it("shows an empty state when no instances are registered", async () => {
    listServiceInstancesMock.mockResolvedValue([]);

    renderRegistryOverview();

    expect(await screen.findByText("Keine Instanzen registriert.")).toBeInTheDocument();
  });
});
