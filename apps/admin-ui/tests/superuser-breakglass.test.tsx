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

vi.mock("@/lib/api", () => ({
  getSuperuserStatus: (...args: unknown[]) => getSuperuserStatusMock(...args),
  requestSuperuserActivation: (...args: unknown[]) => requestSuperuserActivationMock(...args),
  approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
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
      permissions: [],
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
  });

  it("shows the inactive status by default", async () => {
    getSuperuserStatusMock.mockResolvedValue({ active: false, expires_at: null });

    renderComponent();

    expect(await screen.findByText("Inaktiv")).toBeInTheDocument();
  });

  it("shows the active status including the expiry timestamp", async () => {
    getSuperuserStatusMock.mockResolvedValue({
      active: true,
      expires_at: "2026-01-01T00:30:00+00:00",
    });

    renderComponent();

    expect(await screen.findByText("Aktiv", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/2026-01-01T00:30:00\+00:00/)).toBeInTheDocument();
  });

  it("shows an unreachable state when auth-service is not reachable", async () => {
    getSuperuserStatusMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderComponent();

    expect(await screen.findByText("Auth Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("requests activation as the current principal and shows the request id", async () => {
    getSuperuserStatusMock.mockResolvedValue({ active: false, expires_at: null });
    requestSuperuserActivationMock.mockResolvedValue({
      id: "req-1",
      action_type: "auth.superuser.activate",
      initiated_by: "alice",
      status: "pending",
      approved_by: null,
      created_at: "2026-01-01T00:00:00Z",
    });

    renderComponent();
    await screen.findByText("Inaktiv");

    fireEvent.click(screen.getByRole("button", { name: "Aktivierung anfordern" }));

    expect(requestSuperuserActivationMock).toHaveBeenCalledWith("token-123", "alice");
    expect(await screen.findByText(/Freigabe-Request erstellt \(ID: req-1\)/)).toBeInTheDocument();
  });

  it("approves a request by id as the current principal and reloads the status", async () => {
    getSuperuserStatusMock
      .mockResolvedValueOnce({ active: false, expires_at: null })
      .mockResolvedValueOnce({ active: true, expires_at: "2026-01-01T00:30:00+00:00" });
    approveApprovalRequestMock.mockResolvedValue({
      id: "req-1",
      action_type: "auth.superuser.activate",
      initiated_by: "bob",
      status: "approved",
      approved_by: "alice",
      created_at: "2026-01-01T00:00:00Z",
    });

    renderComponent();
    await screen.findByText("Inaktiv");

    fireEvent.change(screen.getByLabelText("Request-ID"), { target: { value: "req-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Genehmigen" }));

    expect(approveApprovalRequestMock).toHaveBeenCalledWith("token-123", "req-1", "alice");
    expect(await screen.findByText("Aktiv", { exact: true })).toBeInTheDocument();
  });
});
