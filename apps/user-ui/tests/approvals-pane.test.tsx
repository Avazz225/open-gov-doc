import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalsPane } from "@/components/ApprovalsPane";
import { I18nProvider } from "@/i18n";

const listApprovalRequestsMock = vi.fn();
const approveApprovalRequestMock = vi.fn();
const rejectApprovalRequestMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listApprovalRequests: (...args: unknown[]) => listApprovalRequestsMock(...args),
    approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
    rejectApprovalRequest: (...args: unknown[]) => rejectApprovalRequestMock(...args),
  };
});

function renderPane(currentUsername = "bob") {
  return render(
    <I18nProvider>
      <ApprovalsPane token="token-123" currentUsername={currentUsername} />
    </I18nProvider>
  );
}

describe("ApprovalsPane", () => {
  beforeEach(() => {
    listApprovalRequestsMock.mockReset();
    approveApprovalRequestMock.mockReset();
    rejectApprovalRequestMock.mockReset();
  });

  it("shows an empty-state message when there are no pending requests", async () => {
    listApprovalRequestsMock.mockResolvedValue([]);

    renderPane();

    expect(await screen.findByText("Keine offenen Löschanträge.")).toBeInTheDocument();
    expect(listApprovalRequestsMock).toHaveBeenCalledWith("token-123", {
      actionType: "document.delete",
      status: "pending",
    });
    expect(listApprovalRequestsMock).toHaveBeenCalledWith("token-123", {
      actionType: "folder.delete",
      status: "pending",
    });
  });

  it("lists pending document and folder requests, disabling approve for one's own request", async () => {
    listApprovalRequestsMock.mockImplementation(async (_token: string, params: { actionType: string }) => {
      if (params.actionType === "document.delete") {
        return [
          {
            id: "req-1",
            action_type: "document.delete",
            initiated_by: "alice",
            payload: { document_id: "doc-1", deleted_by: "alice" },
            status: "pending",
            approved_by: null,
            rejected_by: null,
            reason: null,
            created_at: "2026-01-01T00:00:00Z",
            decided_at: null,
          },
        ];
      }
      return [
        {
          id: "req-2",
          action_type: "folder.delete",
          initiated_by: "bob",
          payload: { folder_id: "folder-1", deleted_by: "bob" },
          status: "pending",
          approved_by: null,
          rejected_by: null,
          reason: null,
          created_at: "2026-01-02T00:00:00Z",
          decided_at: null,
        },
      ];
    });

    renderPane("bob");

    expect(await screen.findByText(/doc-1/)).toBeInTheDocument();
    expect(screen.getByText(/folder-1/)).toBeInTheDocument();

    const approveButtons = screen.getAllByText("Genehmigen") as HTMLButtonElement[];
    // req-1 (initiated_by alice) darf von bob genehmigt werden, req-2
    // (initiated_by bob) nicht - Vier-Augen-Vorwegnahme (5.2, seit P7-S1c).
    expect(approveButtons.some((b) => !b.disabled)).toBe(true);
    expect(approveButtons.some((b) => b.disabled)).toBe(true);
  });

  it("approves a request and reloads the list", async () => {
    listApprovalRequestsMock.mockImplementation(async (_token: string, params: { actionType: string }) => {
      if (params.actionType === "document.delete") {
        return [
          {
            id: "req-1",
            action_type: "document.delete",
            initiated_by: "alice",
            payload: { document_id: "doc-1", deleted_by: "alice" },
            status: "pending",
            approved_by: null,
            rejected_by: null,
            reason: null,
            created_at: "2026-01-01T00:00:00Z",
            decided_at: null,
          },
        ];
      }
      return [];
    });
    approveApprovalRequestMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderPane("bob");
    await screen.findByText(/doc-1/);

    await user.click(screen.getByText("Genehmigen"));

    await waitFor(() =>
      expect(approveApprovalRequestMock).toHaveBeenCalledWith("token-123", "req-1", "bob")
    );
  });
});
