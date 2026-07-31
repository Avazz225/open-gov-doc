import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KennzeichenSettings } from "@/components/KennzeichenSettings";
import { I18nProvider } from "@/i18n";

function renderKennzeichenSettings() {
  return render(
    <I18nProvider>
      <KennzeichenSettings />
    </I18nProvider>
  );
}

const getKennzeichenConfigMock = vi.fn();
const updateKennzeichenConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getKennzeichenConfig: (...args: unknown[]) => getKennzeichenConfigMock(...args),
  updateKennzeichenConfig: (...args: unknown[]) => updateKennzeichenConfigMock(...args),
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

describe("KennzeichenSettings", () => {
  beforeEach(() => {
    getKennzeichenConfigMock.mockReset();
    updateKennzeichenConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getKennzeichenConfigMock.mockResolvedValue({
      show_before_filename: false,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderKennzeichenSettings();

    expect(await screen.findByLabelText("Kennzeichen vor Dateinamen anzeigen")).not.toBeChecked();
  });

  it("shows an unreachable state when object-type-service cannot be reached", async () => {
    getKennzeichenConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderKennzeichenSettings();

    expect(await screen.findByText(/Object-Type Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("saves a changed value", async () => {
    getKennzeichenConfigMock.mockResolvedValue({
      show_before_filename: true,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateKennzeichenConfigMock.mockResolvedValue({
      show_before_filename: false,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderKennzeichenSettings();
    await screen.findByLabelText("Kennzeichen vor Dateinamen anzeigen");

    fireEvent.click(screen.getByLabelText("Kennzeichen vor Dateinamen anzeigen"));
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateKennzeichenConfigMock).toHaveBeenCalledWith("token-123", {
      showBeforeFilename: false,
    });
  });
});
