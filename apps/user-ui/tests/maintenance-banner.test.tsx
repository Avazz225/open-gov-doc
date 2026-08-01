import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MaintenanceBanner } from "@/components/MaintenanceBanner";
import { I18nProvider } from "@/i18n";

const getMaintenanceStatusMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getMaintenanceStatus: (...args: unknown[]) => getMaintenanceStatusMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function renderBanner() {
  return render(
    <I18nProvider>
      <MaintenanceBanner />
    </I18nProvider>
  );
}

describe("MaintenanceBanner", () => {
  beforeEach(() => {
    getMaintenanceStatusMock.mockReset();
  });

  it("renders nothing when maintenance mode is inactive", async () => {
    getMaintenanceStatusMock.mockResolvedValue({ active: false });

    renderBanner();

    await waitFor(() => expect(getMaintenanceStatusMock).toHaveBeenCalled());
    expect(screen.queryByText(/Systemweite Notfallsperre aktiv/)).not.toBeInTheDocument();
  });

  it("shows the banner when maintenance mode is active", async () => {
    getMaintenanceStatusMock.mockResolvedValue({ active: true });

    renderBanner();

    expect(await screen.findByText(/Systemweite Notfallsperre aktiv/)).toBeInTheDocument();
  });

  it("stays quiet when permission-service is unreachable", async () => {
    getMaintenanceStatusMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderBanner();

    await waitFor(() => expect(getMaintenanceStatusMock).toHaveBeenCalled());
    expect(screen.queryByText(/Systemweite Notfallsperre aktiv/)).not.toBeInTheDocument();
  });
});
