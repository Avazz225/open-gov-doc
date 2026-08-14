import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StorageOperationalConfig } from "@/components/StorageOperationalConfig";
import { I18nProvider } from "@/i18n";

function renderStorageOperationalConfig() {
  return render(
    <I18nProvider>
      <StorageOperationalConfig />
    </I18nProvider>
  );
}

const getOperationalConfigMock = vi.fn();
const updateOperationalConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getOperationalConfig: (...args: unknown[]) => getOperationalConfigMock(...args),
  updateOperationalConfig: (...args: unknown[]) => updateOperationalConfigMock(...args),
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

describe("StorageOperationalConfig", () => {
  beforeEach(() => {
    getOperationalConfigMock.mockReset();
    updateOperationalConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getOperationalConfigMock.mockResolvedValue({
      write_strategy: "primary_async",
      quorum_count: 1,
      max_replication_attempts: 5,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderStorageOperationalConfig();

    expect(await screen.findByDisplayValue("1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
  });

  it("shows an unreachable state when storage-service is not reachable", async () => {
    getOperationalConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderStorageOperationalConfig();

    expect(await screen.findByText(/Storage Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("saves changed values", async () => {
    getOperationalConfigMock.mockResolvedValue({
      write_strategy: "primary_async",
      quorum_count: 1,
      max_replication_attempts: 5,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateOperationalConfigMock.mockResolvedValue({
      write_strategy: "quorum",
      quorum_count: 2,
      max_replication_attempts: 3,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderStorageOperationalConfig();
    await screen.findByDisplayValue("1");

    fireEvent.change(screen.getByLabelText("Schreibstrategie"), {
      target: { value: "quorum" },
    });
    fireEvent.change(screen.getByLabelText("Quorum-Anzahl"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Maximale Replikationsversuche"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateOperationalConfigMock).toHaveBeenCalledWith("token-123", {
      writeStrategy: "quorum",
      quorumCount: 2,
      maxReplicationAttempts: 3,
    });
  });

  it("shows an error message when saving fails", async () => {
    getOperationalConfigMock.mockResolvedValue({
      write_strategy: "quorum",
      quorum_count: 2,
      max_replication_attempts: 5,
      updated_at: "2026-01-01T00:00:00Z",
    });
    const { ApiError } = await import("@/lib/api");
    updateOperationalConfigMock.mockRejectedValue(
      new ApiError(422, "quorum_count=2 ist mit 1 konfigurierten Ziel(en) nicht erfüllbar")
    );

    renderStorageOperationalConfig();
    await screen.findByDisplayValue("2");
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(
      await screen.findByText("quorum_count=2 ist mit 1 konfigurierten Ziel(en) nicht erfüllbar")
    ).toBeInTheDocument();
  });
});
