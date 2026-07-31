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
const reidentifyTargetMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getGuardConfig: (...args: unknown[]) => getGuardConfigMock(...args),
  updateGuardConfig: (...args: unknown[]) => updateGuardConfigMock(...args),
  getGuardStatus: (...args: unknown[]) => getGuardStatusMock(...args),
  reidentifyTarget: (...args: unknown[]) => reidentifyTargetMock(...args),
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

describe("StorageGuard", () => {
  beforeEach(() => {
    getGuardConfigMock.mockReset();
    updateGuardConfigMock.mockReset();
    getGuardStatusMock.mockReset();
    reidentifyTargetMock.mockReset();
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

  it("accepts a device change after confirmation and reloads the status", async () => {
    getGuardConfigMock.mockResolvedValue({
      allow_degraded_start: false,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getGuardStatusMock
      .mockResolvedValueOnce([
        { target_id: "local", device_id: "old-device", verified_at: "2026-01-01T00:00:00Z", pending_copies: 0 },
      ])
      .mockResolvedValueOnce([
        { target_id: "local", device_id: "new-device", verified_at: "2026-01-02T00:00:00Z", pending_copies: 3 },
      ]);
    reidentifyTargetMock.mockResolvedValue({
      target_id: "local",
      device_id: "new-device",
      verified_at: "2026-01-02T00:00:00Z",
      pending_copies: 3,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderStorageGuard();
    await screen.findByText("old-device");

    fireEvent.click(screen.getByRole("button", { name: "Datenträger-Wechsel akzeptieren" }));

    expect(await screen.findByText("new-device")).toBeInTheDocument();
    expect(reidentifyTargetMock).toHaveBeenCalledWith("token-123", "local");
  });

  it("does not call reidentify when the confirmation is dismissed", async () => {
    getGuardConfigMock.mockResolvedValue({
      allow_degraded_start: false,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getGuardStatusMock.mockResolvedValue([
      { target_id: "local", device_id: "abc123", verified_at: "2026-01-01T00:00:00Z", pending_copies: 0 },
    ]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderStorageGuard();
    await screen.findByText("local");

    fireEvent.click(screen.getByRole("button", { name: "Datenträger-Wechsel akzeptieren" }));

    expect(reidentifyTargetMock).not.toHaveBeenCalled();
  });
});
