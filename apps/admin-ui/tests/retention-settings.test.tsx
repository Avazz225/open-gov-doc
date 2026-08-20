import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
const getFolderRetentionConfigMock = vi.fn();
const updateFolderRetentionConfigMock = vi.fn();
const getFolderTrashConfigMock = vi.fn();
const updateFolderTrashConfigMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getRetentionConfig: (...args: unknown[]) => getRetentionConfigMock(...args),
  updateRetentionConfig: (...args: unknown[]) => updateRetentionConfigMock(...args),
  getTrashConfig: (...args: unknown[]) => getTrashConfigMock(...args),
  updateTrashConfig: (...args: unknown[]) => updateTrashConfigMock(...args),
  getFolderRetentionConfig: (...args: unknown[]) => getFolderRetentionConfigMock(...args),
  updateFolderRetentionConfig: (...args: unknown[]) => updateFolderRetentionConfigMock(...args),
  getFolderTrashConfig: (...args: unknown[]) => getFolderTrashConfigMock(...args),
  updateFolderTrashConfig: (...args: unknown[]) => updateFolderTrashConfigMock(...args),
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

function documentsCard() {
  return screen.getByText("Dokumente").closest(".card") as HTMLElement;
}

function foldersCard() {
  return screen.getByText("Ordner").closest(".card") as HTMLElement;
}

describe("RetentionSettings", () => {
  beforeEach(() => {
    getRetentionConfigMock.mockReset();
    updateRetentionConfigMock.mockReset();
    getTrashConfigMock.mockReset();
    updateTrashConfigMock.mockReset();
    getFolderRetentionConfigMock.mockReset();
    updateFolderRetentionConfigMock.mockReset();
    getFolderTrashConfigMock.mockReset();
    updateFolderTrashConfigMock.mockReset();

    getFolderRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getFolderTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
  });

  it("loads and shows the current configuration for documents and folders independently", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: true,
      reminder_lead_days: 14,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    getFolderRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: 7,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getFolderTrashConfigMock.mockResolvedValue({
      restore_period_days: 60,
      updated_at: "2026-01-01T00:00:00Z",
    });

    renderRetentionSettings();

    await screen.findByText("Dokumente");
    expect(
      within(documentsCard()).getByLabelText("Löschgrund bei Zwangslöschung verpflichtend")
    ).toBeChecked();
    expect(within(documentsCard()).getByDisplayValue("14")).toBeInTheDocument();
    expect(within(documentsCard()).getByDisplayValue("30")).toBeInTheDocument();

    expect(
      within(foldersCard()).getByLabelText("Löschgrund bei Zwangslöschung verpflichtend")
    ).not.toBeChecked();
    expect(within(foldersCard()).getByDisplayValue("7")).toBeInTheDocument();
    expect(within(foldersCard()).getByDisplayValue("60")).toBeInTheDocument();
  });

  it("shows an unreachable state per section when a service is not reachable", async () => {
    getRetentionConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));
    getTrashConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderRetentionSettings();

    expect(await screen.findByText("Document Service nicht erreichbar.")).toBeInTheDocument();
    // Folder section remains independently functional.
    expect(await within(foldersCard()).findByDisplayValue("30")).toBeInTheDocument();
  });

  it("saves both document configs", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: true,
      reminder_lead_days: 7,
      deletion_reason_catalog: [],
      updated_at: "2026-01-02T00:00:00Z",
    });
    updateTrashConfigMock.mockResolvedValue({
      restore_period_days: 60,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderRetentionSettings();
    await within(documentsCard()).findByDisplayValue("30");

    fireEvent.click(
      within(documentsCard()).getByLabelText("Löschgrund bei Zwangslöschung verpflichtend")
    );
    fireEvent.change(
      within(documentsCard()).getByLabelText("Löscherinnerung, Vorlaufzeit (Tage)"),
      { target: { value: "7" } }
    );
    fireEvent.change(
      within(documentsCard()).getByLabelText("Papierkorb-Wiederherstellungsfrist (Tage)"),
      { target: { value: "60" } }
    );
    fireEvent.click(within(documentsCard()).getByRole("button", { name: "Speichern" }));

    expect(await within(documentsCard()).findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateRetentionConfigMock).toHaveBeenCalledWith("token-123", {
      deletionReasonRequired: true,
      reminderLeadDays: 7,
      deletionReasonCatalog: [],
    });
    expect(updateTrashConfigMock).toHaveBeenCalledWith("token-123", {
      restorePeriodDays: 60,
    });
  });

  it("saves both folder configs independently from documents", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: [],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateFolderRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: true,
      reminder_lead_days: 5,
      deletion_reason_catalog: [],
      updated_at: "2026-01-02T00:00:00Z",
    });
    updateFolderTrashConfigMock.mockResolvedValue({
      restore_period_days: 90,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderRetentionSettings();
    await within(foldersCard()).findByDisplayValue("30");

    fireEvent.click(
      within(foldersCard()).getByLabelText("Löschgrund bei Zwangslöschung verpflichtend")
    );
    fireEvent.change(within(foldersCard()).getByLabelText("Löscherinnerung, Vorlaufzeit (Tage)"), {
      target: { value: "5" },
    });
    fireEvent.change(
      within(foldersCard()).getByLabelText("Papierkorb-Wiederherstellungsfrist (Tage)"),
      { target: { value: "90" } }
    );
    fireEvent.click(within(foldersCard()).getByRole("button", { name: "Speichern" }));

    expect(await within(foldersCard()).findByText("Gespeichert.")).toBeInTheDocument();
    expect(updateFolderRetentionConfigMock).toHaveBeenCalledWith("token-123", {
      deletionReasonRequired: true,
      reminderLeadDays: 5,
      deletionReasonCatalog: [],
    });
    expect(updateFolderTrashConfigMock).toHaveBeenCalledWith("token-123", {
      restorePeriodDays: 90,
    });
    expect(updateRetentionConfigMock).not.toHaveBeenCalled();
  });

  it("adds and removes deletion-reason catalog entries, then saves them (Phase 31)", async () => {
    getRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: ["Dublette"],
      updated_at: "2026-01-01T00:00:00Z",
    });
    getTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-01T00:00:00Z",
    });
    updateRetentionConfigMock.mockResolvedValue({
      deletion_reason_required: false,
      reminder_lead_days: null,
      deletion_reason_catalog: ["Aufbewahrungsfrist abgelaufen"],
      updated_at: "2026-01-02T00:00:00Z",
    });
    updateTrashConfigMock.mockResolvedValue({
      restore_period_days: 30,
      updated_at: "2026-01-02T00:00:00Z",
    });

    renderRetentionSettings();
    await within(documentsCard()).findByText("Dublette");

    // Remove the pre-existing entry.
    fireEvent.click(within(documentsCard()).getByRole("button", { name: "Löschen" }));
    expect(within(documentsCard()).queryByText("Dublette")).not.toBeInTheDocument();

    // Add a new one.
    fireEvent.change(
      within(documentsCard()).getByPlaceholderText("Neuer Löschgrund"),
      { target: { value: "Aufbewahrungsfrist abgelaufen" } }
    );
    fireEvent.click(within(documentsCard()).getByRole("button", { name: "Hinzufügen" }));
    expect(within(documentsCard()).getByText("Aufbewahrungsfrist abgelaufen")).toBeInTheDocument();

    fireEvent.click(within(documentsCard()).getByRole("button", { name: "Speichern" }));

    await waitFor(() =>
      expect(updateRetentionConfigMock).toHaveBeenCalledWith("token-123", {
        deletionReasonRequired: false,
        reminderLeadDays: null,
        deletionReasonCatalog: ["Aufbewahrungsfrist abgelaufen"],
      })
    );
  });
});
