import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TrashPane } from "@/components/TrashPane";
import { I18nProvider } from "@/i18n";

const listDeletedDocumentsGlobalMock = vi.fn();
const listDeletedFoldersGlobalMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listDeletedDocumentsGlobal: (...args: unknown[]) => listDeletedDocumentsGlobalMock(...args),
    listDeletedFoldersGlobal: (...args: unknown[]) => listDeletedFoldersGlobalMock(...args),
  };
});

let mockRealmRoles: string[] = [];
let mockPermissions: string[] = [];
vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "alice", username: "alice", email: null, realm_roles: mockRealmRoles },
      permissions: mockPermissions,
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <TrashPane token="token-123" />
    </I18nProvider>
  );
}

// Department-independent RBAC for the classified-documents trash tab
// (Post-Roadmap Phase 32 Session 4, ADR 0133): the tab's visibility now
// follows `permissions.includes("admin.deletion_classified")` (system-native
// permission-service capability) instead of a `user.realm_roles` string
// check - mirrors `RecordsQuarantinePanel`'s established `admin.
// records_quarantine` test pattern.
describe("TrashPane classified-trash tab visibility", () => {
  beforeEach(() => {
    listDeletedDocumentsGlobalMock.mockReset().mockResolvedValue([]);
    listDeletedFoldersGlobalMock.mockReset().mockResolvedValue([]);
    mockRealmRoles = [];
    mockPermissions = [];
  });

  it("hides both admin tabs for a principal with neither role nor permission", async () => {
    renderPane();
    await waitFor(() => expect(listDeletedDocumentsGlobalMock).toHaveBeenCalled());

    expect(screen.queryByText("Vollständiger Papierkorb")).not.toBeInTheDocument();
    expect(screen.queryByText("Verschlusssachen-Papierkorb")).not.toBeInTheDocument();
  });

  it("shows only the regular admin tab for dms-admin without admin.deletion_classified", async () => {
    mockRealmRoles = ["dms-admin"];
    renderPane();
    await waitFor(() => expect(listDeletedDocumentsGlobalMock).toHaveBeenCalled());

    expect(screen.getByText("Vollständiger Papierkorb")).toBeInTheDocument();
    expect(screen.queryByText("Verschlusssachen-Papierkorb")).not.toBeInTheDocument();
  });

  it("shows the classified-trash tab for a principal with admin.deletion_classified, independent of realm_roles", async () => {
    mockRealmRoles = [];
    mockPermissions = ["admin.deletion_classified"];
    renderPane();
    await waitFor(() => expect(listDeletedDocumentsGlobalMock).toHaveBeenCalled());

    const tab = screen.getByText("Verschlusssachen-Papierkorb");
    expect(tab).toBeInTheDocument();

    fireEvent.click(tab);
    await waitFor(() =>
      expect(listDeletedDocumentsGlobalMock).toHaveBeenLastCalledWith(
        "token-123",
        "admin_classified"
      )
    );
    // Per the concept, the classified-documents trash only knows documents.
    expect(listDeletedFoldersGlobalMock).not.toHaveBeenCalledWith(
      "token-123",
      "admin_classified"
    );
  });
});
