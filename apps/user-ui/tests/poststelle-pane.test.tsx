import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PoststellePane } from "@/components/PoststellePane";
import { I18nProvider } from "@/i18n";

const listInboundMessagesMock = vi.fn();
const confirmInboundMatchMock = vi.fn();
const assignInboundMessageMock = vi.fn();
const rejectInboundMessageMock = vi.fn();
const sendOutboundMessageMock = vi.fn();
const listOutboundMessagesMock = vi.fn();
const listMailboxesMock = vi.fn();
const routeInboundMessageMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listInboundMessages: (...args: unknown[]) => listInboundMessagesMock(...args),
    confirmInboundMatch: (...args: unknown[]) => confirmInboundMatchMock(...args),
    assignInboundMessage: (...args: unknown[]) => assignInboundMessageMock(...args),
    rejectInboundMessage: (...args: unknown[]) => rejectInboundMessageMock(...args),
    sendOutboundMessage: (...args: unknown[]) => sendOutboundMessageMock(...args),
    listOutboundMessages: (...args: unknown[]) => listOutboundMessagesMock(...args),
    listMailboxes: (...args: unknown[]) => listMailboxesMock(...args),
    routeInboundMessage: (...args: unknown[]) => routeInboundMessageMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <PoststellePane token="token-123" />
    </I18nProvider>
  );
}

const proposedMessage = {
  id: "msg-1",
  mailbox_id: "central",
  from_address: "buerger@example.com",
  subject: "Rueckmeldung zu Az: 2026-001",
  body_text: "Hallo",
  received_at: new Date().toISOString(),
  status: "proposed_match" as const,
  match_type: "kennzeichen" as const,
  match_value: "2026-001",
  proposed_target_type: "document" as const,
  proposed_target_id: "doc-1",
  match_candidates: ["2026-001"],
  confirmed_by: null,
  confirmed_at: null,
  rejected_reason: null,
  attachments: [],
  routing_log: [],
};

const unassignedMessage = {
  ...proposedMessage,
  id: "msg-2",
  subject: "Ohne Bezug",
  status: "unassigned" as const,
  match_type: null,
  match_value: null,
  proposed_target_type: null,
  proposed_target_id: null,
  match_candidates: [],
};

describe("PoststellePane", () => {
  beforeEach(() => {
    listInboundMessagesMock.mockReset();
    confirmInboundMatchMock.mockReset();
    assignInboundMessageMock.mockReset();
    rejectInboundMessageMock.mockReset();
    sendOutboundMessageMock.mockReset();
    listOutboundMessagesMock.mockReset();
    listMailboxesMock.mockReset();
    routeInboundMessageMock.mockReset();
    listInboundMessagesMock.mockResolvedValue([]);
    listOutboundMessagesMock.mockResolvedValue([]);
    listMailboxesMock.mockResolvedValue([
      { id: "central", name: "Zentrale Poststelle", kind: "central", owning_group_id: null },
    ]);
  });

  it("shows the empty state when the inbox has nothing unassigned", async () => {
    renderPane();
    expect(await screen.findByText("Kein ungesichteter Zulauf.")).toBeInTheDocument();
  });

  it("lists a message with a proposed match and a confirm button", async () => {
    listInboundMessagesMock.mockResolvedValue([proposedMessage]);

    renderPane();

    expect(await screen.findByText(/Rueckmeldung zu Az: 2026-001/)).toBeInTheDocument();
    expect(screen.getByText("Bestätigen")).toBeInTheDocument();
  });

  it("confirms a proposed match with the prefilled title", async () => {
    listInboundMessagesMock.mockResolvedValueOnce([proposedMessage]).mockResolvedValueOnce([]);
    confirmInboundMatchMock.mockResolvedValue({ ...proposedMessage, status: "confirmed" });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Rueckmeldung zu Az: 2026-001/);
    await user.click(screen.getByText("Bestätigen"));
    await user.click(screen.getByText("Übernehmen"));

    await waitFor(() => expect(confirmInboundMatchMock).toHaveBeenCalled());
    expect(confirmInboundMatchMock).toHaveBeenCalledWith(
      "token-123",
      "msg-1",
      expect.objectContaining({ title: "Rueckmeldung zu Az: 2026-001" })
    );
  });

  it("assigns an unassigned message manually with a folder id", async () => {
    listInboundMessagesMock.mockResolvedValueOnce([unassignedMessage]).mockResolvedValueOnce([]);
    assignInboundMessageMock.mockResolvedValue({ ...unassignedMessage, status: "confirmed" });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Ohne Bezug/);
    await user.click(screen.getByText("Zuordnen"));
    await user.type(screen.getByLabelText("Zielordner"), "root");
    await user.click(screen.getByText("Übernehmen"));

    await waitFor(() => expect(assignInboundMessageMock).toHaveBeenCalled());
    expect(assignInboundMessageMock).toHaveBeenCalledWith(
      "token-123",
      "msg-2",
      expect.objectContaining({ folderId: "root" })
    );
  });

  it("rejects a message after confirmation", async () => {
    listInboundMessagesMock
      .mockResolvedValueOnce([unassignedMessage])
      .mockResolvedValueOnce([]);
    rejectInboundMessageMock.mockResolvedValue({ ...unassignedMessage, status: "rejected" });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Ohne Bezug/);
    await user.click(screen.getByText("Verwerfen"));

    await waitFor(() =>
      expect(rejectInboundMessageMock).toHaveBeenCalledWith("token-123", "msg-2")
    );
  });

  it("switches to the outbox tab and sends a new message", async () => {
    sendOutboundMessageMock.mockResolvedValue({
      id: "out-1",
      to_address: "extern@example.com",
      subject: "Antwort",
      body: "Hallo",
      related_document_id: null,
      related_case_id: null,
      sent_by: "poststelle-1",
      sent_at: new Date().toISOString(),
      status: "sent",
      error_message: null,
    });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Kein ungesichteter Zulauf.");
    await user.click(screen.getByText("Postausgang"));
    await screen.findByText("Noch keine versandte Post.");

    await user.click(screen.getByText("Neue Nachricht"));
    await user.type(screen.getByLabelText("Empfänger"), "extern@example.com");
    await user.type(screen.getByLabelText("Betreff"), "Antwort");
    await user.type(screen.getByLabelText("Text"), "Hallo");
    await user.click(screen.getByText("Senden"));

    await waitFor(() => expect(sendOutboundMessageMock).toHaveBeenCalled());
    expect(sendOutboundMessageMock).toHaveBeenCalledWith(
      "token-123",
      expect.objectContaining({ toAddress: "extern@example.com", subject: "Antwort", body: "Hallo" })
    );
  });

  it("hides the mailbox filter and routing action with only one configured mailbox", async () => {
    listInboundMessagesMock.mockResolvedValue([unassignedMessage]);

    renderPane();

    await screen.findByText(/Ohne Bezug/);
    expect(screen.queryByLabelText("Postfach")).not.toBeInTheDocument();
    expect(screen.queryByText("Weiterleiten")).not.toBeInTheDocument();
  });

  it("routes a message to a different mailbox", async () => {
    listMailboxesMock.mockResolvedValue([
      { id: "central", name: "Zentrale Poststelle", kind: "central", owning_group_id: null },
      {
        id: "finanzen",
        name: "Poststelle Finanzen",
        kind: "departmental",
        owning_group_id: "group-finanzen",
      },
    ]);
    listInboundMessagesMock
      .mockResolvedValueOnce([unassignedMessage])
      .mockResolvedValueOnce([{ ...unassignedMessage, mailbox_id: "finanzen" }]);
    routeInboundMessageMock.mockResolvedValue({ ...unassignedMessage, mailbox_id: "finanzen" });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Ohne Bezug/);
    await user.click(screen.getByText("Weiterleiten"));
    await user.selectOptions(screen.getByLabelText("Zielpostfach"), "finanzen");
    await user.type(screen.getByLabelText("Begründung (optional)"), "Fachbezug");
    await user.click(screen.getAllByText("Weiterleiten")[1]);

    await waitFor(() =>
      expect(routeInboundMessageMock).toHaveBeenCalledWith("token-123", "msg-2", {
        targetMailboxId: "finanzen",
        reason: "Fachbezug",
      })
    );
  });

  it("shows the mailbox filter and offers only the other mailbox as a routing target", async () => {
    listMailboxesMock.mockResolvedValue([
      { id: "central", name: "Zentrale Poststelle", kind: "central", owning_group_id: null },
      {
        id: "finanzen",
        name: "Poststelle Finanzen",
        kind: "departmental",
        owning_group_id: "group-finanzen",
      },
    ]);
    listInboundMessagesMock.mockResolvedValue([unassignedMessage]);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Ohne Bezug/);
    expect(screen.getByLabelText("Postfach")).toBeInTheDocument();

    await user.click(screen.getByText("Weiterleiten"));
    const targetSelect = screen.getByLabelText("Zielpostfach") as HTMLSelectElement;
    const optionLabels = Array.from(targetSelect.options).map((o) => o.text);
    // "central" ist das aktuelle Postfach der Nachricht - nicht als Ziel wählbar.
    expect(optionLabels).not.toContain("Zentrale Poststelle");
    expect(optionLabels).toContain("Poststelle Finanzen");
  });

  it("shows the routing history for a message that has been routed before", async () => {
    listMailboxesMock.mockResolvedValue([
      { id: "central", name: "Zentrale Poststelle", kind: "central", owning_group_id: null },
      {
        id: "finanzen",
        name: "Poststelle Finanzen",
        kind: "departmental",
        owning_group_id: "group-finanzen",
      },
    ]);
    listInboundMessagesMock.mockResolvedValue([
      {
        ...unassignedMessage,
        mailbox_id: "finanzen",
        routing_log: [
          {
            id: 1,
            from_mailbox_id: "central",
            to_mailbox_id: "finanzen",
            routed_by: "poststelle-1",
            routed_at: new Date().toISOString(),
            reason: "Fachbezug Finanzen",
          },
        ],
      },
    ]);

    renderPane();

    expect(
      await screen.findByText("Zentrale Poststelle → Poststelle Finanzen (von poststelle-1)")
    ).toBeInTheDocument();
    expect(screen.getByText(/Fachbezug Finanzen/)).toBeInTheDocument();
  });
});
