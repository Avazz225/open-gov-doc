import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LicenseStatusBanner } from "@/components/LicenseStatusBanner";
import { I18nProvider } from "@/i18n";

const getLicenseStatusMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getLicenseStatus: (...args: unknown[]) => getLicenseStatusMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: [] },
      permissions: ["admin.license"],
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
      <LicenseStatusBanner />
    </I18nProvider>
  );
}

const validStatus = {
  installed: true,
  valid: true,
  invalid_reason: null,
  issued_at: "2026-01-01T00:00:00Z",
  expires_at: "2027-01-01T00:00:00Z",
  days_remaining: 300,
  user_model: "concurrent",
  users: null,
  storage_gb: null,
  documents: null,
  licensed_components: null,
  limits_exceeded: [] as string[],
};

describe("LicenseStatusBanner", () => {
  beforeEach(() => {
    getLicenseStatusMock.mockReset();
  });

  it("renders nothing for a healthy, valid license", async () => {
    getLicenseStatusMock.mockResolvedValue(validStatus);

    renderBanner();

    await waitFor(() => expect(getLicenseStatusMock).toHaveBeenCalled());
    expect(screen.queryByText(/Lizenz/)).not.toBeInTheDocument();
  });

  it("shows a banner when no license is installed", async () => {
    getLicenseStatusMock.mockResolvedValue({ ...validStatus, installed: false, valid: false });

    renderBanner();

    expect(await screen.findByText(/Keine Lizenz installiert/)).toBeInTheDocument();
  });

  it("shows a banner when the license is invalid", async () => {
    getLicenseStatusMock.mockResolvedValue({ ...validStatus, valid: false });

    renderBanner();

    expect(await screen.findByText(/Installierte Lizenz ungültig oder abgelaufen/)).toBeInTheDocument();
  });

  it("shows a banner when a usage limit is exceeded", async () => {
    getLicenseStatusMock.mockResolvedValue({ ...validStatus, limits_exceeded: ["documents"] });

    renderBanner();

    expect(await screen.findByText(/Lizenz-Nutzungsgrenze überschritten: documents/)).toBeInTheDocument();
  });

  it("shows a banner when the license is expiring soon", async () => {
    getLicenseStatusMock.mockResolvedValue({ ...validStatus, days_remaining: 5 });

    renderBanner();

    expect(await screen.findByText(/Lizenz läuft in 5 Tagen ab/)).toBeInTheDocument();
  });

  it("stays quiet when license-service is unreachable", async () => {
    getLicenseStatusMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderBanner();

    await waitFor(() => expect(getLicenseStatusMock).toHaveBeenCalled());
    expect(screen.queryByText(/Lizenz/)).not.toBeInTheDocument();
  });
});
