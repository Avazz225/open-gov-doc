import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalSettings } from "@/components/ApprovalSettings";
import { I18nProvider } from "@/i18n";

function renderApprovalSettings() {
  return render(
    <I18nProvider>
      <ApprovalSettings />
    </I18nProvider>
  );
}

const listApprovalConfigMock = vi.fn();
const putApprovalConfigMock = vi.fn();
const listRolesMock = vi.fn();
const getApprovalRecipientsMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listApprovalConfig: (...args: unknown[]) => listApprovalConfigMock(...args),
  putApprovalConfig: (...args: unknown[]) => putApprovalConfigMock(...args),
  listRoles: (...args: unknown[]) => listRolesMock(...args),
  getApprovalRecipients: (...args: unknown[]) => getApprovalRecipientsMock(...args),
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

const CONFIG_A = {
  action_type: "document.force_unlock",
  requires_approval: false,
  required_permission: null,
  updated_at: "2026-01-01T00:00:00Z",
};

const CONFIG_B = {
  action_type: "auth.superuser.activate",
  requires_approval: true,
  required_permission: "breakglass.approve",
  updated_at: "2026-01-02T00:00:00Z",
};

describe("ApprovalSettings", () => {
  beforeEach(() => {
    listApprovalConfigMock.mockReset();
    putApprovalConfigMock.mockReset();
    listRolesMock
      .mockReset()
      .mockResolvedValue([{ id: 7, name: "Registratur", description: "", permissions: [] }]);
    getApprovalRecipientsMock.mockReset();
  });

  it("shows an empty state without any configured action types", async () => {
    listApprovalConfigMock.mockResolvedValue([]);

    renderApprovalSettings();

    expect(await screen.findByText("Noch keine Aktionstypen konfiguriert.")).toBeInTheDocument();
  });

  it("shows an unreachable state when permission-service is not reachable", async () => {
    listApprovalConfigMock.mockRejectedValue(new TypeError("Failed to fetch"));

    renderApprovalSettings();

    expect(await screen.findByText(/Permission Service nicht erreichbar/)).toBeInTheDocument();
  });

  it("lists configured action types sorted, with required permission and status", async () => {
    listApprovalConfigMock.mockResolvedValue([CONFIG_B, CONFIG_A]);

    renderApprovalSettings();

    const rows = await screen.findAllByRole("row");
    // Kopfzeile + zwei Datenzeilen, alphabetisch sortiert (auth... vor document...).
    expect(within(rows[1]).getByText("auth.superuser.activate")).toBeInTheDocument();
    expect(within(rows[1]).getByText("breakglass.approve")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Aktiv")).toBeInTheDocument();
    expect(within(rows[2]).getByText("document.force_unlock")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Inaktiv")).toBeInTheDocument();
  });

  it("toggles an action type, sending only the flag (P90-S3: the backend keeps the rest)", async () => {
    listApprovalConfigMock
      .mockResolvedValueOnce([CONFIG_B])
      .mockResolvedValueOnce([{ ...CONFIG_B, requires_approval: false }]);
    putApprovalConfigMock.mockResolvedValue({ ...CONFIG_B, requires_approval: false });

    renderApprovalSettings();
    await screen.findByText("auth.superuser.activate");

    const row = screen.getByText("auth.superuser.activate").closest("tr")!;
    fireEvent.click(within(row).getByRole("checkbox"));

    await waitFor(() =>
      expect(putApprovalConfigMock).toHaveBeenCalledWith("token-123", "auth.superuser.activate", {
        requiresApproval: false,
      })
    );
    await waitFor(() => expect(listApprovalConfigMock).toHaveBeenCalledTimes(2));
  });

  it("creates a new action type configuration and reloads", async () => {
    listApprovalConfigMock.mockResolvedValue([]);
    putApprovalConfigMock.mockResolvedValue({
      action_type: "document.delete",
      requires_approval: true,
      required_permission: null,
      updated_at: "2026-01-03T00:00:00Z",
    });

    renderApprovalSettings();
    await waitFor(() => expect(listApprovalConfigMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Aktionstyp konfigurieren" });
    fireEvent.change(within(form).getByLabelText("Aktionstyp"), {
      target: { value: "document.delete" },
    });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(putApprovalConfigMock).toHaveBeenCalledWith("token-123", "document.delete", {
        requiresApproval: true,
        requiredPermission: null,
      })
    );
    await waitFor(() => expect(listApprovalConfigMock).toHaveBeenCalledTimes(2));
  });

  it("shows an error when toggling fails", async () => {
    listApprovalConfigMock.mockResolvedValue([CONFIG_A]);
    putApprovalConfigMock.mockRejectedValue(new Error("boom"));

    renderApprovalSettings();
    await screen.findByText("document.force_unlock");

    const row = screen.getByText("document.force_unlock").closest("tr")!;
    fireEvent.click(within(row).getByRole("checkbox"));

    expect(await screen.findByText("Umschalten fehlgeschlagen")).toBeInTheDocument();
  });

  it("sets the notified role and previews who would be told (P90-S3)", async () => {
    listApprovalConfigMock.mockResolvedValue([CONFIG_B]);
    putApprovalConfigMock.mockResolvedValue({ ...CONFIG_B, recipient_role: "Registratur" });
    getApprovalRecipientsMock.mockResolvedValue({
      action_type: "auth.superuser.activate",
      source: "required_permission",
      recipients: ["bob", "carla"],
      truncated: false,
    });

    renderApprovalSettings();
    await screen.findByText("auth.superuser.activate");
    const row = screen.getByText("auth.superuser.activate").closest("tr")!;

    fireEvent.click(within(row).getByRole("button", { name: "Wer?" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Inhaber der erforderlichen Berechtigung · 2 Person(en)");

    fireEvent.change(
      within(row).getByLabelText("Benachrichtigte Rolle für auth.superuser.activate"),
      { target: { value: "Registratur" } }
    );
    await waitFor(() =>
      expect(putApprovalConfigMock).toHaveBeenCalledWith("token-123", "auth.superuser.activate", {
        requiresApproval: true,
        recipientRole: "Registratur",
      })
    );
    // The open preview is refreshed after the change.
    await waitFor(() => expect(getApprovalRecipientsMock).toHaveBeenCalledTimes(2));
  });
});
