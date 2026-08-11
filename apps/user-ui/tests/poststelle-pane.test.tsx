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
    listInboundMessagesMock.mockResolvedValue([]);
    listOutboundMessagesMock.mockResolvedValue([]);
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
});
