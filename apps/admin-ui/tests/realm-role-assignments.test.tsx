import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RealmRoleAssignments } from "@/components/RealmRoleAssignments";
import { I18nProvider } from "@/i18n";

// P88-S4b: Keycloak realm-role assignment in user management.

const mocks = vi.hoisted(() => ({
  listRealmRoles: vi.fn(),
  listRealmRoleMembers: vi.fn(),
  assignRealmRole: vi.fn(),
  unassignRealmRole: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  listRealmRoles: (...a: unknown[]) => mocks.listRealmRoles(...a),
  listRealmRoleMembers: (...a: unknown[]) => mocks.listRealmRoleMembers(...a),
  assignRealmRole: (...a: unknown[]) => mocks.assignRealmRole(...a),
  unassignRealmRole: (...a: unknown[]) => mocks.unassignRealmRole(...a),
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
      permissions: ["admin.user_management"],
      accessToken: "tok",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

const USERS = [
  { id: "1", username: "anna", email: null, enabled: true, first_name: null, last_name: null },
  { id: "2", username: "bernd", email: null, enabled: true, first_name: null, last_name: null },
];

function renderSection() {
  return render(
    <I18nProvider>
      <RealmRoleAssignments users={USERS} />
    </I18nProvider>
  );
}

describe("RealmRoleAssignments", () => {
  beforeEach(() => {
    mocks.listRealmRoles.mockReset().mockResolvedValue(["dms-poststelle", "dms-admin"]);
    mocks.listRealmRoleMembers.mockReset().mockResolvedValue([{ id: "1", username: "anna" }]);
    mocks.assignRealmRole.mockReset().mockResolvedValue(undefined);
    mocks.unassignRealmRole.mockReset().mockResolvedValue(undefined);
  });

  it("lists roles sorted and expands one to its members", async () => {
    renderSection();
    const buttons = await screen.findAllByRole("button", { expanded: false });
    expect(buttons.map((b) => b.textContent)).toEqual(["dms-admin", "dms-poststelle"]);

    fireEvent.click(screen.getByRole("button", { name: "dms-poststelle" }));
    expect(await screen.findByText("anna")).toBeInTheDocument();
    expect(mocks.listRealmRoleMembers).toHaveBeenCalledWith("tok", "dms-poststelle");
    expect(screen.getByText("(1 Personen)")).toBeInTheDocument();
  });

  it("assigns a role by username, suggesting only users without it", async () => {
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "dms-poststelle" }));
    await screen.findByText("anna");
    const options = [...document.querySelectorAll("#realm-role-candidates-dms-poststelle option")].map(
      (o) => (o as HTMLOptionElement).value
    );
    expect(options).toEqual(["bernd"]);

    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "bernd" } });
    fireEvent.click(screen.getByRole("button", { name: "Zuweisen" }));
    await waitFor(() => expect(mocks.assignRealmRole).toHaveBeenCalledWith("tok", "dms-poststelle", "bernd"));
    // Members are reloaded after the change.
    await waitFor(() => expect(mocks.listRealmRoleMembers).toHaveBeenCalledTimes(2));
  });

  it("offers a filter once the realm holds many roles", async () => {
    mocks.listRealmRoles.mockResolvedValue([
      "dms-poststelle",
      ...Array.from({ length: 11 }, (_, i) => `test-role-${i}`),
    ]);
    renderSection();
    fireEvent.change(await screen.findByLabelText("Rollen filtern"), { target: { value: "post" } });
    expect(screen.getAllByRole("button", { expanded: false }).map((b) => b.textContent)).toEqual(["dms-poststelle"]);
  });

  it("removes a member and shows a server error", async () => {
    const { ApiError } = await import("@/lib/api");
    mocks.unassignRealmRole.mockRejectedValueOnce(new ApiError(403, "Fehlende Domain-Admin-Rolle"));
    renderSection();
    fireEvent.click(await screen.findByRole("button", { name: "dms-poststelle" }));
    const chip = (await screen.findByText("anna")).closest("li")!;
    fireEvent.click(within(chip).getByRole("button", { name: "anna die Rolle dms-poststelle entziehen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Fehlende Domain-Admin-Rolle");
    expect(mocks.unassignRealmRole).toHaveBeenCalledWith("tok", "dms-poststelle", "anna");
  });
});
