import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DelegationsAdmin } from "@/components/DelegationsAdmin";
import { I18nProvider } from "@/i18n";

function renderView() {
  return render(
    <I18nProvider>
      <DelegationsAdmin />
    </I18nProvider>
  );
}

const listAllDelegationsMock = vi.fn();
const revokeDelegationAsAdminMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listAllDelegations: (...args: unknown[]) => listAllDelegationsMock(...args),
  revokeDelegationAsAdmin: (...args: unknown[]) => revokeDelegationAsAdminMock(...args),
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
      permissions: [],
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const now = Date.now();
const ACTIVE_DELEGATION = {
  id: "d1",
  delegator_principal_id: "alice-sub",
  deputy_principal_id: "bob-sub",
  starts_at: new Date(now - 3600_000).toISOString(),
  ends_at: new Date(now + 86400_000).toISOString(),
  scope_object_type_ids: null,
  scope_process_definition_ids: null,
  scope_folder_resource_ids: null,
  created_at: new Date(now - 3600_000).toISOString(),
  revoked_at: null,
  revoked_by: null,
};

const REVOKED_DELEGATION = {
  ...ACTIVE_DELEGATION,
  id: "d2",
  delegator_principal_id: "dave-sub",
  deputy_principal_id: "carol-sub",
  revoked_at: new Date(now - 1800_000).toISOString(),
  revoked_by: "dave-sub",
};

describe("DelegationsAdmin", () => {
  beforeEach(() => {
    listAllDelegationsMock.mockReset();
    revokeDelegationAsAdminMock.mockReset();
  });

  it("shows an empty state when there are no delegations", async () => {
    listAllDelegationsMock.mockResolvedValue([]);
    renderView();

    expect(await screen.findByText("Noch keine Stellvertretungen hinterlegt.")).toBeInTheDocument();
  });

  it("shows an unreachable state when permission-service is not reachable", async () => {
    listAllDelegationsMock.mockRejectedValue(new TypeError("Failed to fetch"));
    renderView();

    expect(await screen.findByText("Permission Service nicht erreichbar.")).toBeInTheDocument();
  });

  it("lists delegations with delegator/deputy/status and a revoke button for active ones", async () => {
    listAllDelegationsMock.mockResolvedValue([ACTIVE_DELEGATION, REVOKED_DELEGATION]);
    renderView();

    expect(await screen.findByText("alice-sub")).toBeInTheDocument();
    expect(screen.getByText("bob-sub")).toBeInTheDocument();
    expect(screen.getByText("carol-sub")).toBeInTheDocument();
    expect(screen.getByText("Aktiv")).toBeInTheDocument();
    // "Widerrufen" erscheint zweimal: als Status-Badge-Text der widerrufenen
    // Zeile UND als Button-Beschriftung der aktiven Zeile - genau ein Button.
    expect(screen.getAllByText("Widerrufen")).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Widerrufen" })).toHaveLength(1);
  });

  it("revokes an active delegation after confirmation", async () => {
    listAllDelegationsMock
      .mockResolvedValueOnce([ACTIVE_DELEGATION])
      .mockResolvedValueOnce([{ ...ACTIVE_DELEGATION, revoked_at: new Date().toISOString() }]);
    revokeDelegationAsAdminMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderView();

    await screen.findByText("alice-sub");
    fireEvent.click(screen.getByRole("button", { name: "Widerrufen" }));

    await waitFor(() =>
      expect(revokeDelegationAsAdminMock).toHaveBeenCalledWith("token-123", "d1")
    );
  });

  it("does not revoke when the confirmation is declined", async () => {
    listAllDelegationsMock.mockResolvedValue([ACTIVE_DELEGATION]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderView();

    await screen.findByText("alice-sub");
    fireEvent.click(screen.getByRole("button", { name: "Widerrufen" }));

    expect(revokeDelegationAsAdminMock).not.toHaveBeenCalled();
  });
});
