import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RetentionSettings } from "@/components/RetentionSettings";
import { I18nProvider } from "@/i18n";

function renderRetentionSettings() {
  return render(
    <I18nProvider>
      <RetentionSettings />
    </I18nProvider>
  );
}

const getRetentionConfigMock = vi.fn();
const updateRetentionConfigMock = vi.fn();
const getTrashConfigMock = vi.fn();
const updateTrashConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getRetentionConfig: (...args: unknown[]) => getRetentionConfigMock(...args),
  updateRetentionConfig: (...args: unknown[]) => updateRetentionConfigMock(...args),
  getTrashConfig: (...args: unknown[]) => getTrashConfigMock(...args),
  updateTrashConfig: (...args: unknown[]) => updateTrashConfigMock(...args),
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

describe("RetentionSettings", () => {
  beforeEach(() => {
    getRetentionConfigMock.mockReset();
    updateRetentionConfigMock.mockReset();
    getTrashConfigMock.mockReset();
    updateTrashConfigMock.mockReset();
  });

  it("loads and shows the current configuration", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: true,
      reminder_lead_days: 14,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderRetentionSettings();

    expect(await screen.findByLabelText("Löschgrund bei Zwangslöschung verpflichtend")).toBeChecked();
    expect(screen.getByDisplayValue("14")).toBeInTheDocument();
    expect(screen.getByDisplayValue("30")).toBeInTheDocument();
  });

  it("shows an unreachable state when document-service is not reachable", async () => {
    getRetentionConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));
    getTrashConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderRetentionSettings();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("saves both configs", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: true,
      reminder_lead_days: 7,
      updated_at: "2026-01-02T00:00:00Z",
    });
    updateTrashConfigMock.mockResolvedValue({
      restore_period_days: 60,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderRetentionSettings();
    await screen.findByDisplayValue("30");

    fireEvent.click(screen.getByLabelText("Löschgrund bei Zwangslöschung verpflichtend"));
    fireEvent.change(screen.getByLabelText("Löscherinnerung, Vorlaufzeit (Tage)"), {
      target: { value: "7" },
    });
    fireEvent.change(screen.getByLabelText("Papierkorb-Wiederherstellungsfrist (Tage)"), {
      target: { value: "60" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

    expect(await screen.findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateRetentionConfigMock).toHaveBeenCalledWith("token-123", {
      deletionReasonRequired: true,
      reminderLeadDays: 7,
    });
    expect(updateTrashConfigMock).toHaveBeenCalledWith("token-123", {
      restorePeriodDays: 60,
    });
  });
});
