import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryConsoleView } from "@/components/QueryConsoleView";
import { I18nProvider } from "@/i18n";

function renderQueryConsoleView() {
  return render(
    <I18nProvider>
      <QueryConsoleView />
    </I18nProvider>
  );
}

const listQueryEventsMock = vi.fn();
const getManipulationModeStatusMock = vi.fn();
const activateManipulationModeMock = vi.fn();
const deactivateManipulationModeMock = vi.fn();
const dryRunManipulationMock = vi.fn();
const executeManipulationMock = vi.fn();
const listPendingManipulationApprovalsMock = vi.fn();
const approveApprovalRequestMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listQueryEvents: (...args: unknown[]) => listQueryEventsMock(...args),
  getManipulationModeStatus: (...args: unknown[]) => getManipulationModeStatusMock(...args),
  activateManipulationMode: (...args: unknown[]) => activateManipulationModeMock(...args),
  deactivateManipulationMode: (...args: unknown[]) => deactivateManipulationModeMock(...args),
  dryRunManipulation: (...args: unknown[]) => dryRunManipulationMock(...args),
  executeManipulation: (...args: unknown[]) => executeManipulationMock(...args),
  listPendingManipulationApprovals: (...args: unknown[]) =>
    listPendingManipulationApprovalsMock(...args),
  approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

let mockPermissions: string[] = ["admin.query_console", "admin.query_console.manipulate"];

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "query-admin", email: null, realm_roles: [] },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("QueryConsoleView", () => {
  beforeEach(() => {
    mockPermissions = ["admin.query_console", "admin.query_console.manipulate"];
    listQueryEventsMock.mockReset();
    getManipulationModeStatusMock.mockReset();
    activateManipulationModeMock.mockReset();
    deactivateManipulationModeMock.mockReset();
    dryRunManipulationMock.mockReset();
    executeManipulationMock.mockReset();
    listPendingManipulationApprovalsMock.mockReset();
    approveApprovalRequestMock.mockReset();

    getManipulationModeStatusMock.mockResolvedValue({
      active: false,
      activated_by: null,
      expires_at: null,
    });
    listPendingManipulationApprovalsMock.mockResolvedValue([]);
  });

  it("shows a hint before any query has been run", () => {
    renderQueryConsoleView();

    expect(screen.getByText("Noch keine Abfrage gestartet.")).toBeInTheDocument();
    expect(listQueryEventsMock).not.toHaveBeenCalled();
  });

  it("queries events with the current filters and renders results", async () => {
    listQueryEventsMock.mockResolvedValue({
      events: [
        {
          id: 1,
          event_type: "document.viewed",
          occurred_at: "2026-08-01T10:00:00Z",
          service_name: "document-service",
          subject: "doc-1",
          actor: "alice",
          payload: {},
        },
      ],
      total_before_filter: 1,
      total_after_filter: 1,
      superuser: false,
    });
    const user = userEvent.setup();

    renderQueryConsoleView();

    await user.type(screen.getByPlaceholderText("Nach Nutzer filtern (Akteur)"), "alice");
    await user.click(screen.getByText("Abfragen"));

    await waitFor(() => expect(listQueryEventsMock).toHaveBeenCalled());
    expect(listQueryEventsMock).toHaveBeenCalledWith(
      "token-123",
      expect.objectContaining({ actor: "alice" })
    );

    expect(await screen.findByText("document.viewed")).toBeInTheDocument();
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("doc-1")).toBeInTheDocument();
    expect(screen.getByText("1 von 1 Ereignissen sichtbar (Rest durch Ihre Berechtigungen ausgeblendet).")).toBeInTheDocument();
  });

  it("shows an empty state when the query returns no visible entries", async () => {
    listQueryEventsMock.mockResolvedValue({
      events: [],
      total_before_filter: 3,
      total_after_filter: 0,
      superuser: false,
    });
    const user = userEvent.setup();

    renderQueryConsoleView();
    await user.click(screen.getByText("Abfragen"));

    expect(await screen.findByText("Keine (sichtbaren) Treffer für die gewählten Filter.")).toBeInTheDocument();
    expect(
      screen.getByText("0 von 3 Ereignissen sichtbar (Rest durch Ihre Berechtigungen ausgeblendet).")
    ).toBeInTheDocument();
  });

  it("shows the superuser hint instead of the filtered-count hint", async () => {
    listQueryEventsMock.mockResolvedValue({
      events: [
        {
          id: 1,
          event_type: "workflow.instance.completed",
          occurred_at: "2026-08-01T10:00:00Z",
          service_name: "workflow-service",
          subject: "instance-1",
          actor: "root-admin",
          payload: {},
        },
      ],
      total_before_filter: 1,
      total_after_filter: 1,
      superuser: true,
    });
    const user = userEvent.setup();

    renderQueryConsoleView();
    await user.click(screen.getByText("Abfragen"));

    expect(await screen.findByText("Superuser-Ansicht — ungefiltert.")).toBeInTheDocument();
  });

  it("shows an error message when the query fails", async () => {
    listQueryEventsMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();

    renderQueryConsoleView();
    await user.click(screen.getByText("Abfragen"));

    expect(await screen.findByText("Abfrage fehlgeschlagen")).toBeInTheDocument();
  });

  describe("Manipulation section", () => {
    it("is hidden without the admin.query_console.manipulate capability", () => {
      mockPermissions = ["admin.query_console"];
      renderQueryConsoleView();

      expect(screen.queryByText("Manipulation")).not.toBeInTheDocument();
    });

    it("shows the Schutzschalter status and lets it be activated", async () => {
      activateManipulationModeMock.mockResolvedValue({
        active: true,
        activated_by: "query-admin",
        expires_at: "2026-08-07T13:00:00Z",
      });
      const user = userEvent.setup();

      renderQueryConsoleView();
      expect(await screen.findByText("Manipulationsmodus inaktiv")).toBeInTheDocument();

      await user.click(screen.getByText("Aktivieren"));

      await waitFor(() => expect(activateManipulationModeMock).toHaveBeenCalledWith("token-123", 15));
      expect(await screen.findByText("Manipulationsmodus aktiv")).toBeInTheDocument();
    });

    it("lets an active Schutzschalter be deactivated", async () => {
      getManipulationModeStatusMock.mockResolvedValue({
        active: true,
        activated_by: "query-admin",
        expires_at: "2026-08-07T13:00:00Z",
      });
      deactivateManipulationModeMock.mockResolvedValue({
        active: false,
        activated_by: null,
        expires_at: null,
      });
      const user = userEvent.setup();

      renderQueryConsoleView();
      expect(await screen.findByText("Manipulationsmodus aktiv")).toBeInTheDocument();

      await user.click(screen.getByText("Deaktivieren"));

      await waitFor(() => expect(deactivateManipulationModeMock).toHaveBeenCalledWith("token-123"));
      expect(await screen.findByText("Manipulationsmodus inaktiv")).toBeInTheDocument();
    });

    it("runs a dry-run for the default action and then executes it immediately", async () => {
      dryRunManipulationMock.mockResolvedValue({
        action_type: "document.attribute_reset",
        preview: "Wuerde Attribut 'notiz' von Dokument 'doc-1' von 'alt' auf null zuruecksetzen.",
        is_critical: false,
        dry_run_token: "token-abc",
      });
      executeManipulationMock.mockResolvedValue({
        status: "executed",
        result: { document_id: "doc-1", attributes: {} },
        approval_request_id: null,
      });
      const user = userEvent.setup();

      renderQueryConsoleView();
      await user.type(screen.getByPlaceholderText("Dokument-ID"), "doc-1");
      await user.type(screen.getByPlaceholderText("Attributschlüssel"), "notiz");
      await user.click(screen.getByText("Simulieren"));

      await waitFor(() => expect(dryRunManipulationMock).toHaveBeenCalled());
      expect(dryRunManipulationMock).toHaveBeenCalledWith("token-123", "document.attribute_reset", {
        document_id: "doc-1",
        attribute_key: "notiz",
      });
      expect(await screen.findByText(/Wuerde Attribut 'notiz'/)).toBeInTheDocument();
      expect(screen.queryByText(/Kritische Aktion/)).not.toBeInTheDocument();

      await user.click(screen.getByText("Ausführen"));

      await waitFor(() => expect(executeManipulationMock).toHaveBeenCalledWith("token-123", "token-abc"));
      expect(await screen.findByText("Ausgeführt.")).toBeInTheDocument();
    });

    it("shows the critical badge and a pending-approval result for a critical action", async () => {
      dryRunManipulationMock.mockResolvedValue({
        action_type: "permission.role_assignment.delete",
        preview: "Wuerde Rollenzuweisung 42 entfernen: ...",
        is_critical: true,
        dry_run_token: "token-critical",
      });
      executeManipulationMock.mockResolvedValue({
        status: "pending_approval",
        result: null,
        approval_request_id: "req-1",
      });
      const user = userEvent.setup();

      renderQueryConsoleView();
      await user.selectOptions(screen.getByDisplayValue("Dokument-Attribut zurücksetzen"), [
        "permission.role_assignment.delete",
      ]);
      await user.type(screen.getByPlaceholderText("Rollenzuweisungs-ID"), "42");
      await user.click(screen.getByText("Simulieren"));

      expect(await screen.findByText(/Kritische Aktion/)).toBeInTheDocument();

      await user.click(screen.getByText("Ausführen"));

      expect(
        await screen.findByText("Freigabe-Request erstellt — eine zweite Person muss ihn genehmigen (siehe Tabelle unten).")
      ).toBeInTheDocument();
      await waitFor(() => expect(listPendingManipulationApprovalsMock).toHaveBeenCalledTimes(2));
    });

    it("rejects invalid JSON in the object-type value field before running a dry-run", async () => {
      const user = userEvent.setup();

      renderQueryConsoleView();
      await user.selectOptions(screen.getByDisplayValue("Dokument-Attribut zurücksetzen"), [
        "object_type.update",
      ]);
      await user.type(screen.getByPlaceholderText("Objekttyp-ID"), "7");
      await user.type(screen.getByPlaceholderText("Neuer Wert (JSON)"), "{{not valid json");
      await user.click(screen.getByText("Simulieren"));

      expect(await screen.findByText("Neuer Wert ist kein gültiges JSON.")).toBeInTheDocument();
      expect(dryRunManipulationMock).not.toHaveBeenCalled();
    });

    it("renders pending approvals and approves one", async () => {
      listPendingManipulationApprovalsMock.mockResolvedValue([
        {
          id: "req-1",
          action_type: "document.attribute_reset",
          initiated_by: "alice",
          payload: { params: { document_id: "doc-1", attribute_key: "notiz" } },
          status: "pending",
          approved_by: null,
          created_at: "2026-08-01T10:00:00Z",
        },
      ]);
      approveApprovalRequestMock.mockResolvedValue({});
      const user = userEvent.setup();

      renderQueryConsoleView();
      expect(await screen.findByText("document.attribute_reset")).toBeInTheDocument();
      expect(screen.getByText("alice")).toBeInTheDocument();

      await user.click(screen.getByText("Genehmigen"));

      await waitFor(() =>
        expect(approveApprovalRequestMock).toHaveBeenCalledWith("token-123", "req-1", "query-admin")
      );
    });

    it("shows the empty state when there are no pending approvals", async () => {
      renderQueryConsoleView();

      expect(await screen.findByText("Keine ausstehenden Genehmigungen.")).toBeInTheDocument();
    });
  });
});
