import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SuperuserBreakGlass } from "@/components/SuperuserBreakGlass";
import { I18nProvider } from "@/i18n";

function renderComponent() {
  return render(
    <I18nProvider>
      <SuperuserBreakGlass />
    </I18nProvider>
  );
}

const getSuperuserStatusMock = vi.fn();
const requestSuperuserActivationMock = vi.fn();
const approveApprovalRequestMock = vi.fn();
const getMaintenanceStatusMock = vi.fn();
const triggerMaintenanceModeMock = vi.fn();
const liftMaintenanceModeMock = vi.fn();

const INACTIVE_MAINTENANCE = {
  active: false,
  reason: null,
  triggered_by: null,
  activated_at: null,
  lifted_by: null,
  lifted_at: null,
};

let mockPermissions: string[] = [];

vi.mock("@/lib/api", () => ({
  getSuperuserStatus: (...args: unknown[]) => getSuperuserStatusMock(...args),
  requestSuperuserActivation: (...args: unknown[]) => requestSuperuserActivationMock(...args),
  approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
  getMaintenanceStatus: (...args: unknown[]) => getMaintenanceStatusMock(...args),
  triggerMaintenanceMode: (...args: unknown[]) => triggerMaintenanceModeMock(...args),
  liftMaintenanceMode: (...args: unknown[]) => liftMaintenanceModeMock(...args),
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
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("SuperuserBreakGlass", () => {
  beforeEach(() => {
    getSuperuserStatusMock.mockReset();
    requestSuperuserActivationMock.mockReset();
    approveApprovalRequestMock.mockReset();
    getMaintenanceStatusMock.mockReset();
    triggerMaintenanceModeMock.mockReset();
    liftMaintenanceModeMock.mockReset();
    getMaintenanceStatusMock.mockResolvedValue(INACTIVE_MAINTENANCE);
    mockPermissions = [];
  });

  it("shows the inactive status by default", async () => {
    getSuperuserStatusMock.mockResolvedValue({
      active: false,
      expires_at: null,
      principal_id: null,
    });

    renderComponent();

    expect(await screen.findAllByText("Inaktiv")).toHaveLength(2);
  });

  it("shows the active status including the expiry timestamp", async () => {
    getSuperuserStatusMock.mockResolvedValue({
      active: true,
      expires_at: "2026-01-01T00:30:00+00:00",
      principal_id: "alice",
    });

    renderComponent();

    await screen.findByText(/2026-01-01T00:30:00\+00:00/);
    const activeLabels = screen.getAllByText("Aktiv", { exact: true });
    expect(activeLabels.length).toBeGreaterThanOrEqual(1);
  });

  it("shows an unreachable state when auth-service is not reachable", async () => {
    getSuperuserStatusMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderComponent();

    expect(await screen.findByText("Auth Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("requests activation as the current principal and shows the request id", async () => {
    getSuperuserStatusMock.mockResolvedValue({
      active: false,
      expires_at: null,
      principal_id: null,
    });
    requestSuperuserActivationMock.mockResolvedValue({
      id: "req-1",
      action_type: "auth.superuser.activate",
      initiated_by: "alice",
      status: "pending",
      approved_by: null,
      created_at: "2026-01-01T00:00:00Z",
    });

    renderComponent();
    await screen.findAllByText("Inaktiv");

    fireEvent.click(screen.getByRole("button", { name: "Aktivierung anfordern" }));

    expect(requestSuperuserActivationMock).toHaveBeenCalledWith("token-123", "alice");
    expect(await screen.findByText(/Freigabe-Request erstellt \(ID: req-1\)/)).toBeInTheDocument();
  });

  it("approves a request by id as the current principal and reloads the status", async () => {
    getSuperuserStatusMock
      .mockResolvedValueOnce({ active: false, expires_at: null, principal_id: null })
      .mockResolvedValueOnce({
        active: true,
        expires_at: "2026-01-01T00:30:00+00:00",
        principal_id: "alice",
      });
    approveApprovalRequestMock.mockResolvedValue({
      id: "req-1",
      action_type: "auth.superuser.activate",
      initiated_by: "bob",
      status: "approved",
      approved_by: "alice",
      created_at: "2026-01-01T00:00:00Z",
    });

    renderComponent();
    await screen.findAllByText("Inaktiv");

    fireEvent.change(screen.getByLabelText("Request-ID"), { target: { value: "req-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));

    await screen.findByText(/2026-01-01T00:30:00\+00:00/);
  });

  it("hides the trigger form without the not-shutdown capability", async () => {
    getSuperuserStatusMock.mockResolvedValue({
      active: false,
      expires_at: null,
      principal_id: null,
    });

    renderComponent();
    await screen.findAllByText("Inaktiv");

    expect(screen.queryByRole("button", { name: "Notfallsperre auslösen" })).not.toBeInTheDocument();
  });

  it("triggers the emergency shutdown with the current principal", async () => {
    mockPermissions = ["system.not_shutdown.trigger"];
    getSuperuserStatusMock.mockResolvedValue({
      active: false,
      expires_at: null,
      principal_id: null,
    });
    triggerMaintenanceModeMock.mockResolvedValue({
      status: "activated",
      maintenance_mode: { ...INACTIVE_MAINTENANCE, active: true, triggered_by: "alice" },
      approval_request_id: null,
    });

    renderComponent();
    await screen.findAllByText("Inaktiv");

    fireEvent.click(screen.getByRole("button", { name: "Notfallsperre auslösen" }));

    expect(triggerMaintenanceModeMock).toHaveBeenCalledWith("token-123", "alice", "");
    expect(await screen.findByText("Notfallsperre aktiviert.")).toBeInTheDocument();
  });

  it("shows the lift button only for the currently active superuser", async () => {
    getMaintenanceStatusMock.mockResolvedValue({
      ...INACTIVE_MAINTENANCE,
      active: true,
      triggered_by: "bob",
    });
    getSuperuserStatusMock.mockResolvedValue({
      active: true,
      expires_at: "2026-01-01T00:30:00+00:00",
      principal_id: "alice",
    });

    renderComponent();

    const liftButton = await screen.findByRole("button", { name: "Notfallsperre aufheben" });
    fireEvent.click(liftButton);

    expect(liftMaintenanceModeMock).toHaveBeenCalledWith("token-123", "alice");
  });
});
