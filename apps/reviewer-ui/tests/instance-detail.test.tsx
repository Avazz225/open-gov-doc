import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InstanceDetail } from "@/components/InstanceDetail";
import { I18nProvider } from "@/i18n";

const getInstanceMock = vi.fn();
const listInstanceTasksMock = vi.fn();
const completeTaskMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getInstance: (...args: unknown[]) => getInstanceMock(...args),
  listInstanceTasks: (...args: unknown[]) => listInstanceTasksMock(...args),
  completeTask: (...args: unknown[]) => completeTaskMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
    permissions: [],
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

const onBackMock = vi.fn();

function renderDetail(instanceId = "instance-1") {
  return render(
    <I18nProvider>
      <InstanceDetail instanceId={instanceId} onBack={onBackMock} />
    </I18nProvider>
  );
}

const INSTANCE = {
  id: "instance-1",
  process_definition_id: 5,
  business_key: "case-42",
  status: "running" as const,
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  completed_at: null,
};

// "Vorgang" direct-link detail view (post-roadmap phase 29, ADR 0109/0110).
describe("InstanceDetail", () => {
  beforeEach(() => {
    getInstanceMock.mockReset();
    listInstanceTasksMock.mockReset();
    completeTaskMock.mockReset();
    onBackMock.mockReset();
  });

  it("shows instance status/business key and its currently-open tasks", async () => {
    getInstanceMock.mockResolvedValue(INSTANCE);
    listInstanceTasksMock.mockResolvedValue([
      { id: "task-1", name: "Rechnung prüfen", lane: "Sachbearbeitung", data: {}, extensions: {} },
    ]);

    renderDetail();

    await waitFor(() => expect(screen.getByText("Läuft")).toBeInTheDocument());
    expect(screen.getByText("case-42")).toBeInTheDocument();
    expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument();
  });

  it("shows an empty state when the instance has no currently-open tasks", async () => {
    getInstanceMock.mockResolvedValue(INSTANCE);
    listInstanceTasksMock.mockResolvedValue([]);

    renderDetail();

    await waitFor(() => expect(screen.getByText(/Keine offenen Aufgaben/)).toBeInTheDocument());
  });

  it("shows a load error, e.g. for an unknown/forbidden instance", async () => {
    getInstanceMock.mockRejectedValue(new Error("nicht gefunden"));
    listInstanceTasksMock.mockResolvedValue([]);

    renderDetail();

    await waitFor(() =>
      expect(screen.getByText("Vorgang konnte nicht geladen werden.")).toBeInTheDocument()
    );
  });

  it("completes a task and reloads the instance", async () => {
    getInstanceMock.mockResolvedValue(INSTANCE);
    listInstanceTasksMock.mockResolvedValue([
      { id: "task-1", name: "Rechnung prüfen", lane: "Sachbearbeitung", data: {}, extensions: {} },
    ]);
    completeTaskMock.mockResolvedValue(undefined);

    renderDetail();
    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() =>
      expect(completeTaskMock).toHaveBeenCalledWith(
        "token-123",
        expect.objectContaining({ instanceId: "instance-1", taskId: "task-1" })
      )
    );
    expect(listInstanceTasksMock).toHaveBeenCalledTimes(2);
  });

  it("calls onBack when the back button is clicked", async () => {
    getInstanceMock.mockResolvedValue(INSTANCE);
    listInstanceTasksMock.mockResolvedValue([]);

    renderDetail();
    await waitFor(() => expect(screen.getByText("Läuft")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "Zurück zu den Aufgaben" }));

    expect(onBackMock).toHaveBeenCalled();
  });
});
