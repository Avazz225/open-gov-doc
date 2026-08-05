import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AuditTraceSettings } from "@/components/AuditTraceSettings";
import { I18nProvider } from "@/i18n";

function renderAuditTraceSettings() {
  return render(
    <I18nProvider>
      <AuditTraceSettings />
    </I18nProvider>
  );
}

const getAuditTraceConfigMock = vi.fn();
const updateAuditTraceConfigMock = vi.fn();
const listAuditTraceRoleOverridesMock = vi.fn();
const putAuditTraceRoleOverrideMock = vi.fn();
const deleteAuditTraceRoleOverrideMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getAuditTraceConfig: (...args: unknown[]) => getAuditTraceConfigMock(...args),
  updateAuditTraceConfig: (...args: unknown[]) => updateAuditTraceConfigMock(...args),
  listAuditTraceRoleOverrides: (...args: unknown[]) => listAuditTraceRoleOverridesMock(...args),
  putAuditTraceRoleOverride: (...args: unknown[]) => putAuditTraceRoleOverrideMock(...args),
  deleteAuditTraceRoleOverride: (...args: unknown[]) => deleteAuditTraceRoleOverrideMock(...args),
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

describe("AuditTraceSettings", () => {
  beforeEach(() => {
    getAuditTraceConfigMock.mockReset().mockResolvedValue({
      log_viewed: true,
      log_downloaded: true,
      updated_at: "2026-08-05T00:00:00Z",
    });
    updateAuditTraceConfigMock.mockReset();
    listAuditTraceRoleOverridesMock.mockReset().mockResolvedValue([]);
    putAuditTraceRoleOverrideMock.mockReset();
    deleteAuditTraceRoleOverrideMock.mockReset();
  });

  it("loads and displays the base config defaulting to both on", async () => {
    renderAuditTraceSettings();

    const viewedCheckbox = await screen.findByLabelText(
      "Lesezugriffe (Metadaten-Ansicht) protokollieren"
    );
    const downloadedCheckbox = screen.getByLabelText("Downloads protokollieren");
    expect(viewedCheckbox).toBeChecked();
    expect(downloadedCheckbox).toBeChecked();
  });

  it("shows an empty state when there are no role overrides", async () => {
    renderAuditTraceSettings();

    expect(await screen.findByText("Keine Rollen-Overrides.")).toBeInTheDocument();
  });

  it("saves changed base config values", async () => {
    updateAuditTraceConfigMock.mockResolvedValue({
      log_viewed: false,
      log_downloaded: true,
      updated_at: "2026-08-05T01:00:00Z",
    });
    const user = userEvent.setup();

    renderAuditTraceSettings();
    const viewedCheckbox = await screen.findByLabelText(
      "Lesezugriffe (Metadaten-Ansicht) protokollieren"
    );
    await user.click(viewedCheckbox);
    await user.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(updateAuditTraceConfigMock).toHaveBeenCalledWith("token-123", false, true)
    );
  });

  it("lists existing role overrides and deletes one", async () => {
    listAuditTraceRoleOverridesMock.mockResolvedValue([
      {
        role: "auditor",
        log_viewed: true,
        log_downloaded: null,
        updated_at: "2026-08-05T00:00:00Z",
      },
    ]);
    deleteAuditTraceRoleOverrideMock.mockResolvedValue(undefined);
    const user = userEvent.setup();

    renderAuditTraceSettings();

    expect(await screen.findByText("auditor")).toBeInTheDocument();
    const row = screen.getByText("auditor").closest("tr") as HTMLElement;
    expect(within(row).getByText("An")).toBeInTheDocument();
    expect(within(row).getByText("Standard")).toBeInTheDocument();

    await user.click(within(row).getByText("Löschen"));

    await waitFor(() =>
      expect(deleteAuditTraceRoleOverrideMock).toHaveBeenCalledWith("token-123", "auditor")
    );
  });

  it("creates a new role override via the form", async () => {
    putAuditTraceRoleOverrideMock.mockResolvedValue({
      role: "quiet-role",
      log_viewed: false,
      log_downloaded: null,
      updated_at: "2026-08-05T00:00:00Z",
    });
    const user = userEvent.setup();

    renderAuditTraceSettings();
    await user.type(await screen.findByPlaceholderText("Rollenname"), "quiet-role");
    await user.click(screen.getByText("Override anlegen"));

    await waitFor(() =>
      expect(putAuditTraceRoleOverrideMock).toHaveBeenCalledWith(
        "token-123",
        "quiet-role",
        null,
        null
      )
    );
  });
});
