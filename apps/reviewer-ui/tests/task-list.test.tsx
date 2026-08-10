import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskList } from "@/components/TaskList";
import { I18nProvider } from "@/i18n";

const listReadyTasksMock = vi.fn();
const completeTaskMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listReadyTasks: (...args: unknown[]) => listReadyTasksMock(...args),
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

function renderList() {
  return render(
    <I18nProvider>
      <TaskList />
    </I18nProvider>
  );
}

const MANUAL_TASK = {
  id: "task-1",
  name: "Rechnung prüfen",
  lane: "Sachbearbeitung",
  data: {},
  extensions: {},
  instance_id: "instance-1",
  process_definition_id: 5,
  business_key: "case-42",
};

const SIGNATURE_TASK = {
  id: "task-2",
  name: "Freigabe unterschreiben",
  lane: null,
  data: { document_id: "doc-1" },
  extensions: { taskType: "signature", requiredLevel: "aes" },
  instance_id: "instance-2",
  process_definition_id: 6,
  business_key: null,
};

describe("TaskList", () => {
  beforeEach(() => {
    listReadyTasksMock.mockReset();
    completeTaskMock.mockReset();
  });

  it("shows an empty state when there are no ready tasks", async () => {
    listReadyTasksMock.mockResolvedValue([]);
    renderList();

    await waitFor(() => expect(screen.getByText(/Keine offenen Aufgaben/)).toBeInTheDocument());
  });

  it("lists ready tasks with their process/business-key context", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    expect(screen.getByText("case-42")).toBeInTheDocument();
    expect(screen.getByText("Sachbearbeitung")).toBeInTheDocument();
  });

  it("marks a signature task and requires a signature ID to complete it", async () => {
    listReadyTasksMock.mockResolvedValue([SIGNATURE_TASK]);
    renderList();

    await waitFor(() =>
      expect(screen.getByText("Freigabe unterschreiben")).toBeInTheDocument()
    );
    expect(screen.getByText("Signatur erforderlich")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    expect(screen.getByLabelText("Signatur-ID")).toBeInTheDocument();
  });

  it("completes a manual task and reloads the list", async () => {
    listReadyTasksMock.mockResolvedValueOnce([MANUAL_TASK]).mockResolvedValueOnce([]);
    completeTaskMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() =>
      expect(completeTaskMock).toHaveBeenCalledWith("token-123", {
        instanceId: "instance-1",
        taskId: "task-1",
        completedBy: "alice",
        data: {},
        signatureId: undefined,
      })
    );
    await waitFor(() => expect(listReadyTasksMock).toHaveBeenCalledTimes(2));
  });

  it("shows an error instead of submitting when the extra data field is not valid JSON", async () => {
    listReadyTasksMock.mockResolvedValue([MANUAL_TASK]);
    renderList();

    await waitFor(() => expect(screen.getByText("Rechnung prüfen")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
    fireEvent.change(screen.getByLabelText(/Zusätzliche Prozessdaten/), {
      target: { value: "{not json" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Abschließen" }));

    await waitFor(() => expect(screen.getByText("Kein gültiges JSON")).toBeInTheDocument());
    expect(completeTaskMock).not.toHaveBeenCalled();
  });
});
