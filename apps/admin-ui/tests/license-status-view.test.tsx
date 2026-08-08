import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LicenseStatusView } from "@/components/LicenseStatusView";
import { I18nProvider } from "@/i18n";

function renderView() {
  return render(
    <I18nProvider>
      <LicenseStatusView />
    </I18nProvider>
  );
}

const getLicenseStatusMock = vi.fn();
const uploadLicenseMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getLicenseStatus: (...args: unknown[]) => getLicenseStatusMock(...args),
  uploadLicense: (...args: unknown[]) => uploadLicenseMock(...args),
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

describe("LicenseStatusView", () => {
  beforeEach(() => {
    getLicenseStatusMock.mockReset();
    uploadLicenseMock.mockReset();
  });

  it("shows a not-installed hint when no license exists", async () => {
    getLicenseStatusMock.mockResolvedValue({
      installed: false,
      valid: false,
      invalid_reason: null,
      issued_at: null,
      expires_at: null,
      days_remaining: null,
      user_model: null,
      users: null,
      storage_gb: null,
      documents: null,
      licensed_components: null,
      limits_exceeded: [],
    });

    renderView();

    expect(await screen.findByText("Keine Lizenz installiert.")).toBeInTheDocument();
  });

  it("shows usage per dimension and exceeded limits for an installed license", async () => {
    getLicenseStatusMock.mockResolvedValue({
      installed: true,
      valid: true,
      invalid_reason: null,
      issued_at: "2026-01-01T00:00:00Z",
      expires_at: "2027-01-01T00:00:00Z",
      days_remaining: 300,
      user_model: "concurrent",
      users: { limit: 10, current: 3, exceeded: false },
      storage_gb: { limit: null, current: 5.5, exceeded: false },
      documents: { limit: 5, current: 34, exceeded: true },
      licensed_components: ["document-service"],
      limits_exceeded: ["documents"],
    });

    renderView();

    expect(await screen.findByText("gültig")).toBeInTheDocument();
    expect(screen.getByText("3 / 10")).toBeInTheDocument();
    expect(screen.getByText("5.5 / ∞")).toBeInTheDocument();
    expect(screen.getByText("34 / 5")).toBeInTheDocument();
    expect(screen.getByText("document-service")).toBeInTheDocument();
    expect(screen.getAllByText("documents").length).toBeGreaterThan(0);
  });

  it("uploads a new license token and refreshes the status", async () => {
    getLicenseStatusMock.mockResolvedValue({
      installed: false,
      valid: false,
      invalid_reason: null,
      issued_at: null,
      expires_at: null,
      days_remaining: null,
      user_model: null,
      users: null,
      storage_gb: null,
      documents: null,
      licensed_components: null,
      limits_exceeded: [],
    });
    uploadLicenseMock.mockResolvedValue({
      installed: true,
      valid: true,
      invalid_reason: null,
      issued_at: "2026-01-01T00:00:00Z",
      expires_at: "2027-01-01T00:00:00Z",
      days_remaining: 300,
      user_model: "named",
      users: null,
      storage_gb: null,
      documents: null,
      licensed_components: null,
      limits_exceeded: [],
    });

    renderView();
    await screen.findByText("Keine Lizenz installiert.");

    fireEvent.change(screen.getByLabelText("Neue Lizenzdatei (signiertes JWT) installieren"), {
      target: { value: "eyJhbGciOi.test.token" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Installieren" }));

    expect(await screen.findByText("Lizenz installiert.")).toBeInTheDocument();
    expect(uploadLicenseMock).toHaveBeenCalledWith("token-123", "eyJhbGciOi.test.token");
    expect(screen.getByText("named")).toBeInTheDocument();
  });
});
