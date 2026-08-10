import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DmnDefinitionList } from "@/components/DmnDefinitionList";
import { I18nProvider } from "@/i18n";

const listDmnDefinitionsMock = vi.fn();
const listDmnDefinitionVersionsMock = vi.fn();
const deleteDmnDefinitionMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDmnDefinitions: (...args: unknown[]) => listDmnDefinitionsMock(...args),
  listDmnDefinitionVersions: (...args: unknown[]) => listDmnDefinitionVersionsMock(...args),
  deleteDmnDefinition: (...args: unknown[]) => deleteDmnDefinitionMock(...args),
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
      <DmnDefinitionList />
    </I18nProvider>
  );
}

const FREIGABESTUFE_V2 = {
  id: 2,
  name: "Freigabestufe",
  version: 2,
  decision_id: "approval-level",
  created_at: "2026-02-01T10:00:00Z",
  updated_at: "2026-02-01T10:00:00Z",
};

const FREIGABESTUFE_V1 = {
  ...FREIGABESTUFE_V2,
  id: 1,
  version: 1,
  created_at: "2026-01-01T10:00:00Z",
  updated_at: "2026-01-01T10:00:00Z",
};

describe("DmnDefinitionList", () => {
  beforeEach(() => {
    mockPermissions = [];
    listDmnDefinitionsMock.mockReset();
    listDmnDefinitionVersionsMock.mockReset();
    deleteDmnDefinitionMock.mockReset();
    listDmnDefinitionsMock.mockResolvedValue([FREIGABESTUFE_V2]);
  });

  it("lists only the latest version per family, including its decision_id", async () => {
    renderList();

    expect(await screen.findByText("Freigabestufe")).toBeInTheDocument();
    expect(screen.getByText("approval-level")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("hides Löschen without the admin.object_config capability", async () => {
    renderList();
    await screen.findByText("Freigabestufe");

    expect(screen.queryByText("Löschen")).not.toBeInTheDocument();
    expect(screen.getByText(/Domain-Admin-Rolle/)).toBeInTheDocument();
  });

  it("shows Löschen with the admin.object_config capability and deletes on confirm", async () => {
    mockPermissions = ["admin.object_config"];
    deleteDmnDefinitionMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderList();
    await screen.findByText("Freigabestufe");

    fireEvent.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteDmnDefinitionMock).toHaveBeenCalledWith("token-123", 2));
  });

  it("loads and toggles the version history for a family", async () => {
    listDmnDefinitionVersionsMock.mockResolvedValue([FREIGABESTUFE_V2, FREIGABESTUFE_V1]);
    renderList();
    await screen.findByText("Freigabestufe");

    fireEvent.click(screen.getByText("Versionen anzeigen"));

    await waitFor(() =>
      expect(listDmnDefinitionVersionsMock).toHaveBeenCalledWith("token-123", "Freigabestufe")
    );
    expect(await screen.findByText(/v1 —/)).toBeInTheDocument();
    expect(screen.getByText(/v2 —/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Versionen ausblenden"));
    expect(screen.queryByText(/v1 —/)).not.toBeInTheDocument();
  });

  it("shows the backend error message when delete fails", async () => {
    mockPermissions = ["admin.object_config"];
    const { ApiError } = await import("@/lib/api");
    deleteDmnDefinitionMock.mockRejectedValue(new ApiError(404, "unbekannt"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderList();
    await screen.findByText("Freigabestufe");

    fireEvent.click(screen.getByText("Löschen"));

    expect(await screen.findByRole("alert")).toHaveTextContent("unbekannt");
  });

  it("links Neu erstellen and Öffnen to the dmn-designer route", async () => {
    renderList();
    await screen.findByText("Freigabestufe");

    expect(screen.getByText("Neu erstellen").closest("a")).toHaveAttribute(
      "href",
      "/dmn-designer"
    );
    const row = screen.getByText("Freigabestufe").closest("tr")!;
    expect(within(row).getByText("Öffnen").closest("a")).toHaveAttribute(
      "href",
      "/dmn-designer?id=2"
    );
  });
});
