import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserManagement } from "@/components/UserManagement";
import { I18nProvider } from "@/i18n";

function renderUserManagement() {
  return render(
    <I18nProvider>
      <UserManagement />
    </I18nProvider>
  );
}

const listUsersMock = vi.fn();
const listRolesMock = vi.fn();
const listRoleAssignmentsMock = vi.fn();
const createUserMock = vi.fn();
const deleteUserMock = vi.fn();
const createRoleMock = vi.fn();
const createRoleAssignmentMock = vi.fn();
const deleteRoleAssignmentMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listUsers: (...args: unknown[]) => listUsersMock(...args),
  listRoles: (...args: unknown[]) => listRolesMock(...args),
  listRoleAssignments: (...args: unknown[]) => listRoleAssignmentsMock(...args),
  createUser: (...args: unknown[]) => createUserMock(...args),
  deleteUser: (...args: unknown[]) => deleteUserMock(...args),
  createRole: (...args: unknown[]) => createRoleMock(...args),
  createRoleAssignment: (...args: unknown[]) => createRoleAssignmentMock(...args),
  deleteRoleAssignment: (...args: unknown[]) => deleteRoleAssignmentMock(...args),
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

describe("UserManagement", () => {
  beforeEach(() => {
    listUsersMock.mockReset();
    listRolesMock.mockReset();
    listRoleAssignmentsMock.mockReset();
    createUserMock.mockReset();
    deleteUserMock.mockReset();
    createRoleMock.mockReset();
    createRoleAssignmentMock.mockReset();
    deleteRoleAssignmentMock.mockReset();

    listUsersMock.mockResolvedValue([
      { id: "u1", username: "alice", email: "alice@example.com", enabled: true, first_name: "Alice", last_name: "A" },
    ]);
    listRolesMock.mockResolvedValue([
      { id: 1, name: "Viewer", description: "", permissions: ["read"] },
    ]);
    listRoleAssignmentsMock.mockResolvedValue([
      { id: 10, principal_type: "user", principal_id: "carol", role_id: 1, resource_id: "root" },
    ]);
  });

  it("lists users, roles and assignments", async () => {
    renderUserManagement();

    expect(await screen.findByText("alice")).toBeInTheDocument();
    expect(screen.getAllByText("Viewer").length).toBeGreaterThan(0);
    expect(screen.getAllByText("root").length).toBeGreaterThan(0);
  });

  it("creates a user and reloads the list", async () => {
    createUserMock.mockResolvedValue({});
    renderUserManagement();
    await waitFor(() => expect(listUsersMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Benutzername"), { target: { value: "bob" } });
    fireEvent.change(screen.getByLabelText("E-Mail"), { target: { value: "bob@example.com" } });
    fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: "secret123" } });
    fireEvent.change(screen.getByLabelText("Vorname"), { target: { value: "Bob" } });
    fireEvent.change(screen.getByLabelText("Nachname"), { target: { value: "B" } });
    fireEvent.submit(screen.getByRole("form", { name: "Nutzer anlegen" }));

    await waitFor(() => expect(createUserMock).toHaveBeenCalledWith("token-123", {
      username: "bob",
      email: "bob@example.com",
      password: "secret123",
      firstName: "Bob",
      lastName: "B",
    }));
    await waitFor(() => expect(listUsersMock).toHaveBeenCalledTimes(2));
  });

  it("deletes a user", async () => {
    deleteUserMock.mockResolvedValue(undefined);
    renderUserManagement();

    await screen.findByText("alice");
    fireEvent.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteUserMock).toHaveBeenCalledWith("token-123", "u1"));
  });

  it("removes a role assignment", async () => {
    deleteRoleAssignmentMock.mockResolvedValue(undefined);
    renderUserManagement();

    await screen.findByText("Entfernen");
    fireEvent.click(screen.getByText("Entfernen"));

    await waitFor(() => expect(deleteRoleAssignmentMock).toHaveBeenCalledWith("token-123", 10));
  });

  it("creates a role assignment and reloads the list", async () => {
    createRoleAssignmentMock.mockResolvedValue({
      status: "created",
      role_assignment: { id: 11, principal_type: "user", principal_id: "bob", role_id: 1, resource_id: "root" },
      approval_request_id: null,
    });
    renderUserManagement();
    await waitFor(() => expect(listRoleAssignmentsMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Rolle zuweisen" });
    fireEvent.change(within(form).getByLabelText("Nutzername"), { target: { value: "bob" } });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "1" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(createRoleAssignmentMock).toHaveBeenCalledWith("token-123", {
        principalType: "user",
        principalId: "bob",
        roleId: 1,
        resourceId: "root",
      })
    );
    await waitFor(() => expect(listRoleAssignmentsMock).toHaveBeenCalledTimes(2));
    expect(
      screen.queryByText(
        "Vier-Augen-Prinzip aktiv - die Zuweisung wartet auf Genehmigung durch eine zweite Person, bevor sie wirksam wird."
      )
    ).not.toBeInTheDocument();
  });

  it("shows a pending-approval hint without reloading when four-eyes is active", async () => {
    createRoleAssignmentMock.mockResolvedValue({
      status: "pending_approval",
      role_assignment: null,
      approval_request_id: "req-1",
    });
    renderUserManagement();
    await waitFor(() => expect(listRoleAssignmentsMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Rolle zuweisen" });
    fireEvent.change(within(form).getByLabelText("Nutzername"), { target: { value: "bob" } });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "1" } });
    fireEvent.submit(form);

    expect(
      await screen.findByText(
        "Vier-Augen-Prinzip aktiv - die Zuweisung wartet auf Genehmigung durch eine zweite Person, bevor sie wirksam wird."
      )
    ).toBeInTheDocument();
    expect(listRoleAssignmentsMock).toHaveBeenCalledTimes(1);
  });
});
