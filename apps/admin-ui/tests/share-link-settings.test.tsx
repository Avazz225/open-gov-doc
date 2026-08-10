import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShareLinkSettings } from "@/components/ShareLinkSettings";
import { I18nProvider } from "@/i18n";

function renderShareLinkSettings() {
  return render(
    <I18nProvider>
      <ShareLinkSettings />
    </I18nProvider>
  );
}

const getShareLinkConfigMock = vi.fn();
const updateShareLinkConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getShareLinkConfig: (...args: unknown[]) => getShareLinkConfigMock(...args),
  updateShareLinkConfig: (...args: unknown[]) => updateShareLinkConfigMock(...args),
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

describe("ShareLinkSettings", () => {
  beforeEach(() => {
    getShareLinkConfigMock.mockReset();
    updateShareLinkConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getShareLinkConfigMock.mockResolvedValue({
      enabled: true,
      max_validity_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderShareLinkSettings();

    expect(await screen.findByDisplayValue("30")).toBeInTheDocument();
    expect(screen.getByLabelText("Freigabelinks erlauben")).toBeChecked();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    getShareLinkConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderShareLinkSettings();

    expect(await screen.findByText(/Document Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("saves changed values", async () => {
    getShareLinkConfigMock.mockResolvedValue({
      enabled: true,
      max_validity_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateShareLinkConfigMock.mockResolvedValue({
      enabled: false,
      max_validity_days: 7,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderShareLinkSettings();
    await screen.findByDisplayValue("30");

    fireEvent.click(screen.getByLabelText("Freigabelinks erlauben"));
    fireEvent.change(screen.getByLabelText("Maximale Gültigkeitsdauer neuer Links (Tage)"), {
      target: { value: "7" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateShareLinkConfigMock).toHaveBeenCalledWith("token-123", {
      enabled: false,
      maxValidityDays: 7,
    });
  });
});
