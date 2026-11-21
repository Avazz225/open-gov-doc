import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { IconRail } from "@/components/IconRail";
import { ReminderModal } from "@/components/ReminderModal";
import { RemindersPane } from "@/components/RemindersPane";
import { I18nProvider } from "@/i18n";
import {
  announceRemindersChanged,
  localInputsToIso,
  presetDate,
  toDateInput,
  toTimeInput,
} from "@/lib/reminders";

// Follow-up reminders UI (P90-S2, ADR 0246).

const listRemindersMock = vi.fn();
const createReminderMock = vi.fn();
const completeReminderMock = vi.fn();
const reopenReminderMock = vi.fn();
const snoozeReminderMock = vi.fn();
const deleteReminderMock = vi.fn();
const countDueRemindersMock = vi.fn();
const getDocumentMock = vi.fn();
const getFolderMock = vi.fn();
const getCaseMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listReminders: (...args: unknown[]) => listRemindersMock(...args),
    createReminder: (...args: unknown[]) => createReminderMock(...args),
    completeReminder: (...args: unknown[]) => completeReminderMock(...args),
    reopenReminder: (...args: unknown[]) => reopenReminderMock(...args),
    snoozeReminder: (...args: unknown[]) => snoozeReminderMock(...args),
    deleteReminder: (...args: unknown[]) => deleteReminderMock(...args),
    countDueReminders: (...args: unknown[]) => countDueRemindersMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    getFolder: (...args: unknown[]) => getFolderMock(...args),
    getCase: (...args: unknown[]) => getCaseMock(...args),
  };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function reminder(overrides: Record<string, unknown> = {}) {
  return {
    id: "r-1",
    user_id: "alice",
    object_type: "document",
    object_id: "doc-1",
    due_at: "2026-11-20T08:00:00Z",
    note: "Antwort prüfen",
    status: "open",
    notified_at: null,
    completed_at: null,
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-01T08:00:00Z",
    is_due: true,
    ...overrides,
  };
}

beforeEach(() => {
  for (const mock of [
    listRemindersMock,
    createReminderMock,
    completeReminderMock,
    reopenReminderMock,
    snoozeReminderMock,
    deleteReminderMock,
    countDueRemindersMock,
    getDocumentMock,
    getFolderMock,
    getCaseMock,
  ]) {
    mock.mockReset();
  }
  getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Bauantrag.pdf" });
  getFolderMock.mockResolvedValue({ id: "f-1", name: "Eingang", parent_id: "root" });
  getCaseMock.mockRejectedValue(new Error("404"));
});

describe("reminder date helpers", () => {
  it("lands presets at 09:00 local and converts local inputs to an instant", () => {
    const from = new Date(2026, 10, 21, 17, 30);
    expect(toTimeInput(presetDate("tomorrow", from))).toBe("09:00");
    expect(toDateInput(presetDate("tomorrow", from))).toBe("2026-11-22");
    expect(toDateInput(presetDate("nextWeek", from))).toBe("2026-11-28");
    expect(toDateInput(presetDate("nextMonth", from))).toBe("2026-12-21");
    expect(localInputsToIso("2026-12-01", "09:00")).toBe(new Date(2026, 11, 1, 9, 0).toISOString());
    expect(localInputsToIso("", "09:00")).toBeNull();
  });
});

describe("RemindersPane", () => {
  function renderPane(onOpenDocument = vi.fn()) {
    return render(
      <I18nProvider>
        <RemindersPane
          token="token-123"
          currentUsername="alice"
          onOpenDocument={onOpenDocument}
          onOpenFolder={vi.fn()}
          onOpenCase={vi.fn()}
        />
      </I18nProvider>
    );
  }

  it("groups due, upcoming and done, resolving names and keeping unresolvable ones", async () => {
    listRemindersMock.mockImplementation((_t: string, _u: string, filters: { status: string }) =>
      Promise.resolve(
        filters.status === "open"
          ? [
              reminder(),
              reminder({
                id: "r-2",
                object_type: "folder",
                object_id: "f-1",
                due_at: "2099-01-01T08:00:00Z",
                note: "",
                is_due: false,
              }),
              reminder({ id: "r-3", object_type: "case", object_id: "gone", note: "", is_due: true }),
            ]
          : [reminder({ id: "r-4", status: "done", completed_at: "2026-11-21T10:00:00Z", is_due: false })]
      )
    );
    const onOpenDocument = vi.fn();
    renderPane(onOpenDocument);

    const due = (await screen.findByRole("heading", { name: "Fällig (2)" })).closest("section") as HTMLElement;
    expect(within(due).getByText(/Bauantrag\.pdf/)).toBeInTheDocument();
    expect(within(due).getByText("Antwort prüfen")).toBeInTheDocument();
    // A case that can't be resolved stays listed, so it can still be completed.
    expect(within(due).getByText(/Unbekannt|nicht mehr verfügbar/i)).toBeInTheDocument();
    const upcoming = screen.getByRole("heading", { name: "Anstehend (1)" }).closest("section") as HTMLElement;
    expect(within(upcoming).getByText(/Eingang/)).toBeInTheDocument();
    expect(screen.getByText("Zuletzt erledigt (1)")).toBeInTheDocument();

    await userEvent.click(within(due).getAllByRole("button", { name: "Öffnen" })[0]);
    expect(onOpenDocument).toHaveBeenCalledWith(expect.objectContaining({ id: "doc-1" }));
  });

  it("completes, snoozes to tomorrow 09:00, deletes and reopens, then reloads", async () => {
    listRemindersMock.mockImplementation((_t: string, _u: string, filters: { status: string }) =>
      Promise.resolve(
        filters.status === "open"
          ? [reminder()]
          : [reminder({ id: "r-9", status: "done", completed_at: "2026-11-21T10:00:00Z", is_due: false })]
      )
    );
    completeReminderMock.mockResolvedValue({});
    snoozeReminderMock.mockResolvedValue({});
    deleteReminderMock.mockResolvedValue(undefined);
    reopenReminderMock.mockResolvedValue({});
    const changed = vi.fn();
    window.addEventListener("dms:reminders-changed", changed);
    renderPane();

    const due = (await screen.findByRole("heading", { name: "Fällig (1)" })).closest("section") as HTMLElement;
    await userEvent.click(within(due).getByRole("button", { name: "Erledigt" }));
    await waitFor(() => expect(completeReminderMock).toHaveBeenCalledWith("token-123", "r-1"));

    await userEvent.click(
      await within(due).findByRole("button", { name: "„Bauantrag.pdf“ auf morgen verschieben" })
    );
    await waitFor(() => expect(snoozeReminderMock).toHaveBeenCalled());
    const until = new Date(snoozeReminderMock.mock.calls[0][2]);
    expect([until.getHours(), until.getMinutes()]).toEqual([9, 0]);

    await userEvent.click(within(due).getByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(deleteReminderMock).toHaveBeenCalledWith("token-123", "r-1"));

    await userEvent.click(screen.getByRole("button", { name: "Wieder öffnen" }));
    await waitFor(() => expect(reopenReminderMock).toHaveBeenCalledWith("token-123", "r-9"));
    expect(changed).toHaveBeenCalledTimes(4);
    window.removeEventListener("dms:reminders-changed", changed);
  });
});

describe("ReminderModal", () => {
  it("sets a reminder from a preset with a note and lists the open ones on the object", async () => {
    listRemindersMock.mockResolvedValue([]);
    createReminderMock.mockResolvedValue(reminder());
    render(
      <I18nProvider>
        <ReminderModal objectType="folder" objectId="f-1" objectName="Eingang" onClose={vi.fn()} />
      </I18nProvider>
    );

    const dialog = await screen.findByRole("dialog", { name: "Wiedervorlage: Eingang" });
    await waitFor(() =>
      expect(listRemindersMock).toHaveBeenCalledWith("token-123", "alice", {
        status: "open",
        objectType: "folder",
        objectId: "f-1",
      })
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Morgen" }));
    expect(within(dialog).getByLabelText("Uhrzeit")).toHaveValue("09:00");
    await userEvent.type(within(dialog).getByLabelText("Notiz"), "Ablage prüfen");
    listRemindersMock.mockResolvedValue([reminder({ object_type: "folder", object_id: "f-1", note: "Ablage prüfen" })]);
    await userEvent.click(within(dialog).getByRole("button", { name: "Wiedervorlage setzen" }));

    await waitFor(() => expect(createReminderMock).toHaveBeenCalled());
    const params = createReminderMock.mock.calls[0][1];
    expect(params).toMatchObject({ user_id: "alice", object_type: "folder", object_id: "f-1", note: "Ablage prüfen" });
    expect(params.due_at).toBe(presetDate("tomorrow").toISOString());
    expect(await within(dialog).findByText("Bereits gesetzt")).toBeInTheDocument();
    expect(within(dialog).getByRole("status")).toHaveTextContent("Gesetzt für");
  });
});

describe("IconRail reminder badge", () => {
  it("shows the due count and refreshes when reminders change", async () => {
    countDueRemindersMock.mockResolvedValueOnce(3).mockResolvedValueOnce(2);
    render(
      <I18nProvider>
        <IconRail activeView="documents" onSelectView={vi.fn()} />
      </I18nProvider>
    );
    expect(await screen.findByRole("button", { name: "Wiedervorlagen (3 fällig)" })).toBeInTheDocument();
    announceRemindersChanged();
    expect(await screen.findByRole("button", { name: "Wiedervorlagen (2 fällig)" })).toBeInTheDocument();
  });

  it("shows no badge when nothing is due", async () => {
    countDueRemindersMock.mockResolvedValue(0);
    render(
      <I18nProvider>
        <IconRail activeView="reminders" onSelectView={vi.fn()} />
      </I18nProvider>
    );
    await waitFor(() => expect(countDueRemindersMock).toHaveBeenCalledWith("token-123", "alice"));
    expect(screen.getByRole("button", { name: "Wiedervorlagen" })).toHaveAttribute("aria-current", "page");
  });
});
