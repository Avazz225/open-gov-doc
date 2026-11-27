import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeadLetters } from "@/components/DeadLetters";
import { I18nProvider } from "@/i18n";

// P99-S2 (ADR 0270): undeliverable events on the monitoring page.

const mocks = vi.hoisted(() => ({
  listDeadLetters: vi.fn(),
  getDeadLetter: vi.fn(),
  replayDeadLetter: vi.fn(),
  discardDeadLetter: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  listDeadLetters: (...a: unknown[]) => mocks.listDeadLetters(...a),
  getDeadLetter: (...a: unknown[]) => mocks.getDeadLetter(...a),
  replayDeadLetter: (...a: unknown[]) => mocks.replayDeadLetter(...a),
  discardDeadLetter: (...a: unknown[]) => mocks.discardDeadLetter(...a),
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
      permissions: ["admin.monitoring"],
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const LETTER = {
  seq: 7,
  stream: "folder",
  consumer: "permission-service",
  subject: "folder.resource.moved",
  stream_seq: "41",
  deliveries: 12,
  error: "IntegrityError: parent missing",
  failed_at: "2026-11-27T12:00:00+00:00",
  replayed_from: null,
  size: 80,
};

function renderSection(canManage = true) {
  return render(
    <I18nProvider>
      <DeadLetters canManage={canManage} />
    </I18nProvider>
  );
}

describe("DeadLetters", () => {
  beforeEach(() => {
    mocks.listDeadLetters.mockReset().mockResolvedValue([LETTER]);
    mocks.getDeadLetter.mockReset().mockResolvedValue({
      ...LETTER,
      payload: '{"event_type": "folder.resource.moved"}',
      payload_truncated: false,
    });
    mocks.replayDeadLetter.mockReset().mockResolvedValue(LETTER);
    mocks.discardDeadLetter.mockReset().mockResolvedValue(LETTER);
  });

  it("lists a letter and shows its event", async () => {
    renderSection();
    expect(await screen.findByText("IntegrityError: parent missing")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Anzeigen" }));
    expect(await screen.findByText(/"folder.resource.moved"/)).toBeInTheDocument();
  });

  it("replays with a reason and reloads", async () => {
    renderSection();
    await screen.findByText("IntegrityError: parent missing");
    fireEvent.click(screen.getByRole("button", { name: "Erneut zustellen" }));
    const submit = screen.getByRole("button", { name: "Zustellen" });
    expect(submit).toBeDisabled(); // no reason yet
    fireEvent.change(screen.getByLabelText("Warum es jetzt klappen sollte"), {
      target: { value: "Ordner angelegt" },
    });
    mocks.listDeadLetters.mockResolvedValue([]);
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.replayDeadLetter).toHaveBeenCalledWith("tok", 7, "Ordner angelegt")
    );
    expect(await screen.findByRole("status")).toHaveTextContent("permission-service");
    expect(await screen.findByText("Keine unzustellbaren Ereignisse.")).toBeInTheDocument();
  });

  it("discards with a reason", async () => {
    renderSection();
    await screen.findByText("IntegrityError: parent missing");
    fireEvent.click(screen.getByRole("button", { name: "Verwerfen" }));
    fireEvent.change(screen.getByLabelText("Warum das Ereignis verworfen wird"), {
      target: { value: "Test-Altlast" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Endgültig verwerfen" }));
    await waitFor(() =>
      expect(mocks.discardDeadLetter).toHaveBeenCalledWith("tok", 7, "Test-Altlast")
    );
  });

  it("doesn't load anything without the monitoring role", async () => {
    renderSection(false);
    expect(await screen.findByText(/Nur mit der Rolle/)).toBeInTheDocument();
    expect(mocks.listDeadLetters).not.toHaveBeenCalled();
    expect(
      within(document.body).queryByRole("button", { name: "Verwerfen" })
    ).not.toBeInTheDocument();
  });
});
