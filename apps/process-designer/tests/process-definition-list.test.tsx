import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessDefinitionList } from "@/components/ProcessDefinitionList";
import { I18nProvider } from "@/i18n";

const listProcessDefinitionsMock = vi.fn();
const listProcessDefinitionVersionsMock = vi.fn();
const deleteProcessDefinitionMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listProcessDefinitions: (...args: unknown[]) => listProcessDefinitionsMock(...args),
  listProcessDefinitionVersions: (...args: unknown[]) => listProcessDefinitionVersionsMock(...args),
  deleteProcessDefinition: (...args: unknown[]) => deleteProcessDefinitionMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

let mockPermissions: string[] = [];

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
    permissions: mockPermissions,
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

function renderList() {
  return render(
    <I18nProvider>
      <ProcessDefinitionList />
    </I18nProvider>
  );
}

const RECHNUNGSFREIGABE_V2 = {
  id: 2,
  name: "Rechnungsfreigabe",
  version: 2,
  bpmn_process_id: "Process_Rechnungsfreigabe",
  created_at: "2026-02-01T10:00:00Z",
  updated_at: "2026-02-01T10:00:00Z",
};

const RECHNUNGSFREIGABE_V1 = {
  ...RECHNUNGSFREIGABE_V2,
  id: 1,
  version: 1,
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:00:00Z",
};

describe("ProcessDefinitionList", () => {
  beforeEach(() => {
    mockPermissions = [];
    listProcessDefinitionsMock.mockReset();
    listProcessDefinitionVersionsMock.mockReset();
    deleteProcessDefinitionMock.mockReset();
    listProcessDefinitionsMock.mockResolvedValue([RECHNUNGSFREIGABE_V2]);
  });

  it("lists only the latest version per family", async () => {
    renderList();

    expect(await screen.findByText("Rechnungsfreigabe")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("hides Löschen without the admin.object_config capability", async () => {
    renderList();
    await screen.findByText("Rechnungsfreigabe");

    expect(screen.queryByText("Löschen")).not.toBeInTheDocument();
    expect(screen.getByText(/Domain-Admin-Rolle/)).toBeInTheDocument();
  });

  it("shows Löschen with the admin.object_config capability and deletes on confirm", async () => {
    mockPermissions = ["admin.object_config"];
    deleteProcessDefinitionMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderList();
    await screen.findByText("Rechnungsfreigabe");

    fireEvent.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteProcessDefinitionMock).toHaveBeenCalledWith("token-123", 2));
  });

  it("loads and toggles the version history for a family", async () => {
    listProcessDefinitionVersionsMock.mockResolvedValue([
      RECHNUNGSFREIGABE_V2,
      RECHNUNGSFREIGABE_V1,
    ]);
    renderList();
    await screen.findByText("Rechnungsfreigabe");

    fireEvent.click(screen.getByText("Versionen anzeigen"));

    await waitFor(() =>
      expect(listProcessDefinitionVersionsMock).toHaveBeenCalledWith("token-123", "Rechnungsfreigabe")
    );
    expect(await screen.findByText(/v1 —/)).toBeInTheDocument();
    expect(screen.getByText(/v2 —/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Versionen ausblenden"));
    expect(screen.queryByText(/v1 —/)).not.toBeInTheDocument();
  });

  it("shows the backend error message when delete fails (e.g. definition still in use)", async () => {
    mockPermissions = ["admin.object_config"];
    const { ApiError } = await import("@/lib/api");
    deleteProcessDefinitionMock.mockRejectedValue(new ApiError(409, "Definition wird noch verwendet"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderList();
    await screen.findByText("Rechnungsfreigabe");

    fireEvent.click(screen.getByText("Löschen"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Definition wird noch verwendet");
  });

  it("links Neu erstellen and Öffnen to the designer route", async () => {
    renderList();
    await screen.findByText("Rechnungsfreigabe");

    // `next/link` only applies the `trailingSlash:true` configuration
    // (next.config.mjs) within the real Next.js router, not under Vitest/jsdom -
    // this therefore checks against the path actually rendered in this
    // environment (without a trailing slash).
    expect(screen.getByText("Neu erstellen").closest("a")).toHaveAttribute("href", "/designer");
    const row = screen.getByText("Rechnungsfreigabe").closest("tr")!;
    expect(within(row).getByText("Öffnen").closest("a")).toHaveAttribute(
      "href",
      "/designer?id=2"
    );
  });
});
