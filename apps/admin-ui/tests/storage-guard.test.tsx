import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StorageGuard } from "@/components/StorageGuard";
import { I18nProvider } from "@/i18n";

function renderStorageGuard() {
  return render(
    <I18nProvider>
      <StorageGuard />
    </I18nProvider>
  );
}

const getGuardConfigMock = vi.fn();
const updateGuardConfigMock = vi.fn();
const getGuardStatusMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getGuardConfig: (...args: unknown[]) => getGuardConfigMock(...args),
  updateGuardConfig: (...args: unknown[]) => updateGuardConfigMock(...args),
  getGuardStatus: (...args: unknown[]) => getGuardStatusMock(...args),
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
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("StorageGuard", () => {
  beforeEach(() => {
    getGuardConfigMock.mockReset();
    updateGuardConfigMock.mockReset();
    getGuardStatusMock.mockReset();
  });

  it("shows target status including a resyncing badge for pending copies", async () => {
    getGuardConfigMock.mockResolvedValue({
      allow_degraded_start: false,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getGuardStatusMock.mockResolvedValue([
      {
        target_id: "local",
        device_id: "abc123",
        verified_at: "2026-01-01T00:00:00Z",
        pending_copies: 0,
      },
      {
        target_id: "s3-secondary",
        device_id: "def456",
        verified_at: "2026-01-01T00:00:00Z",
        pending_copies: 7,
      },
    ]);

    renderStorageGuard();

    expect(await screen.findByText("local")).toBeInTheDocument();
    expect(screen.getByText("synchron")).toBeInTheDocument();
    expect(screen.getByText("wird nachrepliziert (7)")).toBeInTheDocument();
  });

  it("shows an unreachable state when storage-service is not reachable", async () => {
    getGuardConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));
    getGuardStatusMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderStorageGuard();

    expect(await screen.findByText(/Storage Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("saves the admin override toggle", async () => {
    getGuardConfigMock.mockResolvedValue({
      allow_degraded_start: false,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getGuardStatusMock.mockResolvedValue([
      { target_id: "local", device_id: "abc123", verified_at: "2026-01-01T00:00:00Z", pending_copies: 0 },
    ]);
    updateGuardConfigMock.mockResolvedValue({
      allow_degraded_start: true,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderStorageGuard();
    await screen.findByText("local");

    fireEvent.click(
      screen.getByLabelText(
        "Degradierten Start erlauben, wenn mindestens ein Ziel nachweislich unverändert ist"
      )
    );
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateGuardConfigMock).toHaveBeenCalledWith("token-123", true);
  });
});
