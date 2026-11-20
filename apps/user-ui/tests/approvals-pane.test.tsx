import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApprovalsPane } from "@/components/ApprovalsPane";
import { I18nProvider } from "@/i18n";

const listApprovalRequestsMock = vi.fn();
const approveApprovalRequestMock = vi.fn();
const rejectApprovalRequestMock = vi.fn();
const getDocumentMock = vi.fn();
const getFolderMock = vi.fn();
const lookupUserByIdMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listApprovalRequests: (...args: unknown[]) => listApprovalRequestsMock(...args),
    approveApprovalRequest: (...args: unknown[]) => approveApprovalRequestMock(...args),
    rejectApprovalRequest: (...args: unknown[]) => rejectApprovalRequestMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    getFolder: (...args: unknown[]) => getFolderMock(...args),
    lookupUserById: (...args: unknown[]) => lookupUserByIdMock(...args),
  };
});

function renderPane(currentPrincipalId = "bob") {
  return render(
    <I18nProvider>
      <ApprovalsPane token="token-123" currentPrincipalId={currentPrincipalId} />
    </I18nProvider>
  );
}

describe("ApprovalsPane", () => {
  beforeEach(() => {
    listApprovalRequestsMock.mockReset();
    approveApprovalRequestMock.mockReset();
    rejectApprovalRequestMock.mockReset();
    // Default: names cannot be resolved, so rows fall back to the short id
    getDocumentMock.mockReset().mockRejectedValue(new Error("not readable"));
    getFolderMock.mockReset().mockRejectedValue(new Error("not readable"));
  });

  it("shows the object's name as the row title, and a short id when it cannot be resolved (P83-S7)", async () => {
    const longFolderId = "5d02b942-0a98-4ffb-8d3e-0f7d98b3f116";
    listApprovalRequestsMock.mockImplementation(async (_token: string, params: { actionType: string }) =>
      params.actionType === "document.delete"
        ? [{ id: "req-1", action_type: "document.delete", initiated_by: "alice", payload: { document_id: "doc-1" }, status: "pending", approved_by: null, rejected_by: null, reason: null, created_at: "2026-01-02T00:00:00Z", decided_at: null }]
        : [{ id: "req-2", action_type: "folder.delete", initiated_by: "alice", payload: { folder_id: longFolderId }, status: "pending", approved_by: null, rejected_by: null, reason: null, created_at: "2026-01-01T00:00:00Z", decided_at: null }]
    );
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung_Mai.pdf" });

    renderPane("bob");

    expect(await screen.findByText("Rechnung_Mai.pdf")).toBeInTheDocument();
    expect(getDocumentMock).toHaveBeenCalledWith("token-123", "doc-1");
    const shortId = await screen.findByText(longFolderId.slice(0, 8));
    expect(shortId.tagName).toBe("CODE");
    expect(shortId.getAttribute("title")).toBe(longFolderId);
    expect(screen.queryByText(longFolderId)).not.toBeInTheDocument();
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
    // req-1 (initiated_by alice) may be approved by bob, req-2
    // (initiated_by bob) may not - four-eyes principle anticipation (5.2, since P7-S1c).
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
      expect(approveApprovalRequestMock).toHaveBeenCalledWith("token-123", "req-1")
    );
  });

  it("shows the initiator's name for a principal id, keeps the id on hover and compares by principal (P86-S2)", async () => {
    const aliceSub = "0b6c2d8e-1111-4a5b-9c3d-000000000001";
    const bobSub = "0b6c2d8e-2222-4a5b-9c3d-000000000002";
    listApprovalRequestsMock.mockImplementation(async (_token: string, params: { actionType: string }) =>
      params.actionType === "document.delete"
        ? [{ id: "req-a", action_type: "document.delete", initiated_by: aliceSub, payload: { document_id: "doc-a" }, status: "pending", approved_by: null, rejected_by: null, reason: null, created_at: "2026-01-02T00:00:00Z", decided_at: null }]
        : [{ id: "req-b", action_type: "folder.delete", initiated_by: bobSub, payload: { folder_id: "folder-b" }, status: "pending", approved_by: null, rejected_by: null, reason: null, created_at: "2026-01-01T00:00:00Z", decided_at: null }]
    );
    lookupUserByIdMock.mockImplementation(async (_token: string, id: string) => ({
      id,
      username: id === aliceSub ? "alice" : "bob",
    }));

    renderPane(bobSub);

    const aliceLabel = await screen.findByText("beantragt von alice");
    expect(aliceLabel.getAttribute("title")).toBe(aliceSub);
    expect(await screen.findByText("beantragt von bob")).toBeInTheDocument();
    const approveButtons = screen.getAllByText("Genehmigen") as HTMLButtonElement[];
    // req-a (alice) first by created_at: approvable; req-b is bob's own.
    expect(approveButtons[0].disabled).toBe(false);
    expect(approveButtons[1].disabled).toBe(true);
  });

  it("rejects with an optional reason (P86-S2)", async () => {
    listApprovalRequestsMock.mockImplementation(async (_token: string, params: { actionType: string }) =>
      params.actionType === "document.delete"
        ? [{ id: "req-r", action_type: "document.delete", initiated_by: "alice", payload: { document_id: "doc-r" }, status: "pending", approved_by: null, rejected_by: null, reason: null, created_at: "2026-01-02T00:00:00Z", decided_at: null }]
        : []
    );
    rejectApprovalRequestMock.mockResolvedValue({});

    const user = userEvent.setup();
    renderPane("bob");
    await screen.findByText(/doc-r/);

    await user.click(screen.getByText("Ablehnen"));
    await user.type(screen.getByLabelText("Begründung (optional)"), "Noch in Prüfung");
    await user.click(screen.getByText("Ablehnung senden"));

    await waitFor(() =>
      expect(rejectApprovalRequestMock).toHaveBeenCalledWith("token-123", "req-r", "Noch in Prüfung")
    );
  });
});
