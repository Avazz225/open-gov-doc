import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MailboxSettings, defaultPort, toInput } from "@/components/MailboxSettings";
import { I18nProvider } from "@/i18n";

// P88-S4b (ADR 0244): the mailbox admin page.

const mocks = vi.hoisted(() => ({
  listMailboxConfig: vi.fn(),
  createMailboxConfig: vi.fn(),
  updateMailboxConfig: vi.fn(),
  deleteMailboxConfig: vi.fn(),
  testMailboxConfig: vi.fn(),
  listGroups: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...Object.fromEntries(
    Object.keys(mocks).map((name) => [
      name,
      (...a: unknown[]) => (mocks as Record<string, (...x: unknown[]) => unknown>)[name](...a),
    ])
  ),
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
      permissions: ["admin.mail_config"],
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const CENTRAL = {
  id: "central",
  name: "Hauptpostfach",
  kind: "central" as const,
  owning_group_id: null,
  inbound_protocol: "pop3" as const,
  host: "mailpit",
  port: 1110,
  username: "mailconnector",
  has_password: true,
  use_tls: false,
  imap_mailbox: "INBOX",
  enabled: true,
  message_count: 744,
  updated_at: "2026-11-21T14:54:06Z",
  updated_by: "system:adopted-from-env",
  last_poll_at: "2026-11-21T14:55:00Z",
  last_poll_ok: false,
  last_poll_error: "error_proto: -ERR invalid password",
};

function renderPage() {
  return render(
    <I18nProvider>
      <MailboxSettings />
    </I18nProvider>
  );
}

describe("MailboxSettings", () => {
  beforeEach(() => {
    Object.values(mocks).forEach((m) => m.mockReset());
    mocks.listMailboxConfig.mockResolvedValue([CENTRAL]);
    mocks.listGroups.mockResolvedValue([{ id: "g-fin", name: "Finanzen", description: "", created_at: "", is_org_unit: true }]);
    mocks.createMailboxConfig.mockResolvedValue({});
    mocks.updateMailboxConfig.mockResolvedValue({});
    mocks.deleteMailboxConfig.mockResolvedValue(undefined);
  });

  it("lists mailboxes with connection, poll result and message count, never a password", async () => {
    renderPage();
    const row = (await screen.findByText("Hauptpostfach")).closest("tr")!;
    expect(within(row).getByText(/POP3 mailpit:1110/)).toBeInTheDocument();
    expect(within(row).getByText("Fehler")).toBeInTheDocument();
    expect(within(row).getByText("error_proto: -ERR invalid password")).toBeInTheDocument();
    expect(within(row).getByText("744")).toBeInTheDocument();
    // With messages: disable, but no delete.
    expect(within(row).getByRole("button", { name: "Deaktivieren" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Löschen" })).not.toBeInTheDocument();
  });

  it("edits without retyping the password: an empty field keeps the stored one", async () => {
    renderPage();
    const row = (await screen.findByText("Hauptpostfach")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Bearbeiten" }));
    const form = screen.getByRole("form", { name: "Postfach bearbeiten" });
    expect(within(form).getByLabelText("Kennung")).toBeDisabled();
    expect(within(form).getByLabelText("Passwort")).toHaveAttribute("placeholder", "unverändert");
    expect(within(form).getByLabelText("Passwort")).not.toBeRequired();
    fireEvent.change(within(form).getByLabelText("Server"), { target: { value: "mail.example.test" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(mocks.updateMailboxConfig).toHaveBeenCalledWith(
        "tok",
        expect.objectContaining({ id: "central", host: "mail.example.test", password: null, port: 1110 })
      )
    );
  });

  it("creates a departmental mailbox with a group from the list and tests the connection", async () => {
    mocks.testMailboxConfig.mockResolvedValue({ ok: false, error: "LOGIN failed" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Postfach anlegen" }));
    const form = screen.getByRole("form", { name: "Neues Postfach" });
    fireEvent.change(within(form).getByLabelText("Kennung"), { target: { value: "finanzen" } });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Poststelle Finanzen" } });
    fireEvent.change(within(form).getByLabelText("Art"), { target: { value: "departmental" } });
    fireEvent.change(await within(form).findByLabelText("Zuständige Gruppe"), { target: { value: "g-fin" } });
    fireEvent.change(within(form).getByLabelText("Server"), { target: { value: "imap.example.test" } });
    fireEvent.change(within(form).getByLabelText("Benutzername"), { target: { value: "fin" } });
    fireEvent.change(within(form).getByLabelText("Passwort"), { target: { value: "geheim" } });
    expect(within(form).getByLabelText("Port")).toHaveAttribute("placeholder", "993");

    fireEvent.click(within(form).getByRole("button", { name: "Verbindung testen" }));
    expect(await within(form).findByRole("status")).toHaveTextContent("Anmeldung fehlgeschlagen");
    expect(within(form).getByText("LOGIN failed")).toBeInTheDocument();

    fireEvent.submit(form);
    await waitFor(() =>
      expect(mocks.createMailboxConfig).toHaveBeenCalledWith("tok", {
        id: "finanzen",
        name: "Poststelle Finanzen",
        kind: "departmental",
        owning_group_id: "g-fin",
        inbound_protocol: "imap",
        host: "imap.example.test",
        port: null,
        username: "fin",
        password: "geheim",
        use_tls: true,
        imap_mailbox: "INBOX",
        enabled: true,
      })
    );
  });

  it("disables a mailbox with messages and deletes one without", async () => {
    mocks.listMailboxConfig.mockResolvedValue([CENTRAL, { ...CENTRAL, id: "leer", name: "Leer", message_count: 0 }]);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const central = (await screen.findByText("Hauptpostfach")).closest("tr")!;
    fireEvent.click(within(central).getByRole("button", { name: "Deaktivieren" }));
    await waitFor(() =>
      expect(mocks.updateMailboxConfig).toHaveBeenCalledWith("tok", expect.objectContaining({ id: "central", enabled: false, password: null }))
    );
    const empty = screen.getByText("Leer").closest("tr")!;
    fireEvent.click(within(empty).getByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(mocks.deleteMailboxConfig).toHaveBeenCalledWith("tok", "leer"));
  });

  it("falls back to a group-id field when groups can't be listed", async () => {
    mocks.listGroups.mockRejectedValue(new Error("down"));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Postfach anlegen" }));
    const form = screen.getByRole("form", { name: "Neues Postfach" });
    fireEvent.change(within(form).getByLabelText("Art"), { target: { value: "departmental" } });
    expect(within(form).getByLabelText("Zuständige Gruppe").tagName).toBe("INPUT");
  });

  it("computes default ports and drops the group for central mailboxes", () => {
    expect([defaultPort("pop3", true), defaultPort("pop3", false), defaultPort("imap", false)]).toEqual([995, 110, 143]);
    const input = toInput({
      id: " x ",
      name: "X",
      kind: "central",
      owningGroupId: "g",
      protocol: "pop3",
      host: "h",
      port: "",
      username: "u",
      password: "",
      useTls: true,
      imapMailbox: "",
      enabled: true,
    });
    expect(input).toMatchObject({ id: "x", owning_group_id: null, port: null, password: null, imap_mailbox: "INBOX" });
  });
});
