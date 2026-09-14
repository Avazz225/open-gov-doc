import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ExportSettings } from "@/components/ExportSettings";
import { I18nProvider } from "@/i18n";
import { ApiError } from "@/lib/api";

function renderExportSettings() {
  return render(
    <I18nProvider>
      <ExportSettings />
    </I18nProvider>
  );
}

const getExportConfigMock = vi.fn();
const updateExportConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getExportConfig: (...args: unknown[]) => getExportConfigMock(...args),
  updateExportConfig: (...args: unknown[]) => updateExportConfigMock(...args),
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

describe("ExportSettings", () => {
  beforeEach(() => {
    getExportConfigMock.mockReset();
    updateExportConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getExportConfigMock.mockResolvedValue({
      history_position: "after",
      stamp_enabled: true,
      stamp_type: "qr",
      stamp_value_template: "{kennzeichen}",
      stamp_position: "bottom-right",
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderExportSettings();

    expect(await screen.findByDisplayValue("{kennzeichen}")).toBeInTheDocument();
    expect(screen.getByLabelText("Automatische Stempelung aktivieren")).toBeChecked();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    getExportConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderExportSettings();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("saves changed values", async () => {
    getExportConfigMock.mockResolvedValue({
      history_position: "after",
      stamp_enabled: false,
      stamp_type: "qr",
      stamp_value_template: "{kennzeichen}",
      stamp_position: "bottom-right",
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateExportConfigMock.mockResolvedValue({
      history_position: "before",
      stamp_enabled: true,
      stamp_type: "barcode",
      stamp_value_template: "{document_id}",
      stamp_position: "top-left",
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderExportSettings();
    await screen.findByDisplayValue("{kennzeichen}");

    fireEvent.change(screen.getByLabelText("Position der Versionshistorie im Export"), {
      target: { value: "before" },
    });
    fireEvent.click(screen.getByLabelText("Automatische Stempelung aktivieren"));
    fireEvent.change(screen.getByLabelText("Stempel-Typ"), { target: { value: "barcode" } });
    fireEvent.change(screen.getByLabelText("Stempel-Position"), { target: { value: "top-left" } });
    fireEvent.change(screen.getByLabelText("Stempel-Inhalt (Vorlage)"), {
      target: { value: "{document_id}" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateExportConfigMock).toHaveBeenCalledWith("token-123", {
      historyPosition: "before",
      stampEnabled: true,
      stampType: "barcode",
      stampValueTemplate: "{document_id}",
      stampPosition: "top-left",
    });
  });

  it("shows the server-side validation error on an invalid stamp combination", async () => {
    getExportConfigMock.mockResolvedValue({
      history_position: "after",
      stamp_enabled: true,
      stamp_type: "qr",
      stamp_value_template: "{kennzeichen}",
      stamp_position: "bottom-right",
      updated_at: "2026-01-01T00:00:00Z",
    });

    updateExportConfigMock.mockRejectedValue(
      new ApiError(422, "stamp_position 'diagonal-center' ist nur für stamp_type='text' verfügbar")
    );

    renderExportSettings();
    await screen.findByDisplayValue("{kennzeichen}");

    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(
      await screen.findByText("stamp_position 'diagonal-center' ist nur für stamp_type='text' verfügbar")
    ).toBeInTheDocument();
  });
});
