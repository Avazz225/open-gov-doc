import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalList } from "@/components/ApprovalList";
import { I18nProvider } from "@/i18n";

const listApprovalRequestsMock = vi.fn();
const approveRequestMock = vi.fn();
const rejectRequestMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listApprovalRequests: (...args: unknown[]) => listApprovalRequestsMock(...args),
  approveRequest: (...args: unknown[]) => approveRequestMock(...args),
  rejectRequest: (...args: unknown[]) => rejectRequestMock(...args),
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
      <ApprovalList />
    </I18nProvider>
  );
}

const PENDING_REQUEST = {
  id: "req-1",
  action_type: "document.delete",
  initiated_by: "bob",
  payload: { document_id: "doc-1" },
  status: "pending" as const,
  approved_by: null,
  rejected_by: null,
  reason: null,
  created_at: "2026-01-01T10:00:00Z",
  decided_at: null,
};

const promptMock = vi.fn<(message?: string) => string | null>();

describe("ApprovalList", () => {
  beforeEach(() => {
    listApprovalRequestsMock.mockReset();
    approveRequestMock.mockReset();
    rejectRequestMock.mockReset();
    promptMock.mockReset().mockReturnValue("");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.prompt = promptMock;
  });

  it("defaults to showing only pending requests, spanning every action type", async () => {
    listApprovalRequestsMock.mockResolvedValue([PENDING_REQUEST]);
    renderList();

    await waitFor(() =>
      expect(listApprovalRequestsMock).toHaveBeenCalledWith("token-123", { status: "pending" })
    );
    expect(screen.getByText("document.delete")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
  });

  it("shows an empty state when there are no matching requests", async () => {
    listApprovalRequestsMock.mockResolvedValue([]);
    renderList();

    await waitFor(() =>
      expect(screen.getByText(/Keine Freigabeanfragen/)).toBeInTheDocument()
    );
  });

  it("expands the raw payload on demand", async () => {
    listApprovalRequestsMock.mockResolvedValue([PENDING_REQUEST]);
    renderList();

    await waitFor(() => expect(screen.getByText("document.delete")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Details anzeigen" }));

    expect(screen.getByText(/"document_id": "doc-1"/)).toBeInTheDocument();
  });

  it("approves a request as the current user after confirmation", async () => {
    listApprovalRequestsMock.mockResolvedValue([PENDING_REQUEST]);
    approveRequestMock.mockResolvedValue({ ...PENDING_REQUEST, status: "approved" });
    renderList();

    await waitFor(() => expect(screen.getByText("document.delete")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Freigeben" }));

    await waitFor(() =>
      expect(approveRequestMock).toHaveBeenCalledWith("token-123", "req-1", "alice")
    );
    expect(listApprovalRequestsMock).toHaveBeenCalledTimes(2);
  });

  it("rejects a request with an optional reason", async () => {
    listApprovalRequestsMock.mockResolvedValue([PENDING_REQUEST]);
    rejectRequestMock.mockResolvedValue({ ...PENDING_REQUEST, status: "rejected" });
    promptMock.mockReturnValue("nicht plausibel");
    renderList();

    await waitFor(() => expect(screen.getByText("document.delete")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Ablehnen" }));

    await waitFor(() =>
      expect(rejectRequestMock).toHaveBeenCalledWith("token-123", "req-1", {
        rejectedBy: "alice",
        reason: "nicht plausibel",
      })
    );
  });

  it("does not offer approve/reject actions for an already-decided request", async () => {
    listApprovalRequestsMock.mockResolvedValue([
      { ...PENDING_REQUEST, status: "approved" as const, approved_by: "carol" },
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText("document.delete")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Freigeben" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ablehnen" })).not.toBeInTheDocument();
  });
});
