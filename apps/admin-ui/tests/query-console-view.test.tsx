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

vi.mock("@/lib/api", () => ({
  listQueryEvents: (...args: unknown[]) => listQueryEventsMock(...args),
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
      user: { sub: "u1", username: "query-admin", email: null, realm_roles: [] },
      permissions: ["admin.query_console"],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

describe("QueryConsoleView", () => {
  beforeEach(() => {
    listQueryEventsMock.mockReset();
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
});
