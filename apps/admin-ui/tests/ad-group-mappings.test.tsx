import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdGroupMappings } from "@/components/AdGroupMappings";
import { I18nProvider } from "@/i18n";

function renderAdGroupMappings() {
  return render(
    <I18nProvider>
      <AdGroupMappings />
    </I18nProvider>
  );
}

const listAdGroupMappingsMock = vi.fn();
const createAdGroupMappingMock = vi.fn();
const deleteAdGroupMappingMock = vi.fn();
const listAdGroupCompositeRulesMock = vi.fn();
const createAdGroupCompositeRuleMock = vi.fn();
const deleteAdGroupCompositeRuleMock = vi.fn();
const listRolesMock = vi.fn();
const getAdGroupMappingDefaultRoleMock = vi.fn();
const setAdGroupMappingDefaultRoleMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listAdGroupMappings: (...args: unknown[]) => listAdGroupMappingsMock(...args),
  createAdGroupMapping: (...args: unknown[]) => createAdGroupMappingMock(...args),
  deleteAdGroupMapping: (...args: unknown[]) => deleteAdGroupMappingMock(...args),
  listAdGroupCompositeRules: (...args: unknown[]) => listAdGroupCompositeRulesMock(...args),
  createAdGroupCompositeRule: (...args: unknown[]) => createAdGroupCompositeRuleMock(...args),
  deleteAdGroupCompositeRule: (...args: unknown[]) => deleteAdGroupCompositeRuleMock(...args),
  listRoles: (...args: unknown[]) => listRolesMock(...args),
  getAdGroupMappingDefaultRole: (...args: unknown[]) => getAdGroupMappingDefaultRoleMock(...args),
  setAdGroupMappingDefaultRole: (...args: unknown[]) => setAdGroupMappingDefaultRoleMock(...args),
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

const ROLES = [
  { id: 1, name: "dms-poststelle", description: "", permissions: [] },
  { id: 2, name: "dms-sachbearbeiter", description: "", permissions: [] },
];

const MAPPING_A = {
  id: 1,
  ad_group_name: "AD-Poststelle",
  role_name: "dms-poststelle",
  created_at: "2026-01-01T00:00:00Z",
  created_by: "admin",
};

const COMPOSITE_RULE_A = {
  id: 1,
  role_name: "dms-sachbearbeiter",
  ad_group_names: ["AD-Team-A", "AD-Standort-Berlin"],
  created_at: "2026-01-01T00:00:00Z",
  created_by: "admin",
};

const DEFAULT_ROLE_EMPTY = { default_role_name: null, updated_at: "2026-01-01T00:00:00Z", updated_by: null };

describe("AdGroupMappings", () => {
  beforeEach(() => {
    listAdGroupMappingsMock.mockReset();
    createAdGroupMappingMock.mockReset();
    deleteAdGroupMappingMock.mockReset();
    listAdGroupCompositeRulesMock.mockReset();
    createAdGroupCompositeRuleMock.mockReset();
    deleteAdGroupCompositeRuleMock.mockReset();
    listRolesMock.mockReset();
    getAdGroupMappingDefaultRoleMock.mockReset();
    setAdGroupMappingDefaultRoleMock.mockReset();

    listRolesMock.mockResolvedValue(ROLES);
    getAdGroupMappingDefaultRoleMock.mockResolvedValue(DEFAULT_ROLE_EMPTY);
  });

  it("shows empty states without any mappings or rules", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);

    renderAdGroupMappings();

    expect(await screen.findByText("Keine AD-Gruppen-Zuordnungen hinterlegt.")).toBeInTheDocument();
    expect(screen.getByText("Keine Verbund-Regeln hinterlegt.")).toBeInTheDocument();
  });

  it("lists existing mappings and composite rules", async () => {
    listAdGroupMappingsMock.mockResolvedValue([MAPPING_A]);
    listAdGroupCompositeRulesMock.mockResolvedValue([COMPOSITE_RULE_A]);

    renderAdGroupMappings();

    expect(await screen.findByText("AD-Poststelle")).toBeInTheDocument();
    expect(screen.getByText("AD-Team-A, AD-Standort-Berlin")).toBeInTheDocument();
  });

  it("creates a new mapping and reloads", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    createAdGroupMappingMock.mockResolvedValue({ status: "created", mapping: MAPPING_A, approval_request_id: null });

    renderAdGroupMappings();
    await waitFor(() => expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Zuordnung anlegen" });
    fireEvent.change(within(form).getByLabelText("AD-Gruppenname"), {
      target: { value: "AD-Poststelle" },
    });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-poststelle" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(createAdGroupMappingMock).toHaveBeenCalledWith("token-123", {
        adGroupName: "AD-Poststelle",
        roleName: "dms-poststelle",
      })
    );
    await waitFor(() => expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(2));
  });

  it("shows a pending-approval hint instead of reloading when four-eyes defers the mapping", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    createAdGroupMappingMock.mockResolvedValue({
      status: "pending_approval",
      mapping: null,
      approval_request_id: "req-1",
    });

    renderAdGroupMappings();
    await waitFor(() => expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Zuordnung anlegen" });
    fireEvent.change(within(form).getByLabelText("AD-Gruppenname"), {
      target: { value: "AD-Neu" },
    });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-poststelle" } });
    fireEvent.submit(form);

    expect(
      await screen.findByText(
        "Vier-Augen-Prinzip aktiv - die Änderung wartet auf Genehmigung durch eine zweite Person, bevor sie wirksam wird."
      )
    ).toBeInTheDocument();
    // Not reloaded - the list would remain unchanged anyway.
    expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(1);
  });

  it("deletes a mapping and reloads", async () => {
    listAdGroupMappingsMock.mockResolvedValue([MAPPING_A]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    deleteAdGroupMappingMock.mockResolvedValue({ status: "deleted", approval_request_id: null });

    renderAdGroupMappings();
    await screen.findByText("AD-Poststelle");

    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(deleteAdGroupMappingMock).toHaveBeenCalledWith("token-123", 1));
    await waitFor(() => expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(2));
  });

  it("rejects a composite rule with fewer than two AD group names client-side", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);

    renderAdGroupMappings();
    await waitFor(() => expect(listAdGroupCompositeRulesMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Verbund-Regel anlegen" });
    fireEvent.change(within(form).getByLabelText("AD-Gruppennamen"), {
      target: { value: "nur-eine-gruppe" },
    });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-sachbearbeiter" } });
    fireEvent.submit(form);

    expect(
      await screen.findByText("Mindestens zwei unterschiedliche AD-Gruppennamen erforderlich.")
    ).toBeInTheDocument();
    expect(createAdGroupCompositeRuleMock).not.toHaveBeenCalled();
  });

  it("creates a composite rule from a comma-separated list, deduplicated and trimmed", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    createAdGroupCompositeRuleMock.mockResolvedValue({
      status: "created",
      rule: COMPOSITE_RULE_A,
      approval_request_id: null,
    });

    renderAdGroupMappings();
    await waitFor(() => expect(listAdGroupCompositeRulesMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Verbund-Regel anlegen" });
    fireEvent.change(within(form).getByLabelText("AD-Gruppennamen"), {
      target: { value: " AD-Team-A ,AD-Standort-Berlin, AD-Team-A" },
    });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-sachbearbeiter" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(createAdGroupCompositeRuleMock).toHaveBeenCalledWith("token-123", {
        roleName: "dms-sachbearbeiter",
        adGroupNames: ["AD-Team-A", "AD-Standort-Berlin"],
      })
    );
  });

  it("deletes a composite rule and reloads", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([COMPOSITE_RULE_A]);
    deleteAdGroupCompositeRuleMock.mockResolvedValue({ status: "deleted", approval_request_id: null });

    renderAdGroupMappings();
    await screen.findByText("AD-Team-A, AD-Standort-Berlin");

    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() => expect(deleteAdGroupCompositeRuleMock).toHaveBeenCalledWith("token-123", 1));
    await waitFor(() => expect(listAdGroupCompositeRulesMock).toHaveBeenCalledTimes(2));
  });

  it("loads and saves the default-role setting", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    getAdGroupMappingDefaultRoleMock.mockResolvedValue({
      default_role_name: null,
      updated_at: "2026-01-01T00:00:00Z",
      updated_by: null,
    });
    setAdGroupMappingDefaultRoleMock.mockResolvedValue({
      status: "set",
      config: {
        default_role_name: "dms-poststelle",
        updated_at: "2026-01-02T00:00:00Z",
        updated_by: "admin",
      },
      approval_request_id: null,
    });

    renderAdGroupMappings();
    await waitFor(() => expect(getAdGroupMappingDefaultRoleMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Standardrolle festlegen" });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-poststelle" } });
    fireEvent.submit(form);

    await waitFor(() =>
      expect(setAdGroupMappingDefaultRoleMock).toHaveBeenCalledWith("token-123", "dms-poststelle")
    );
    expect(await screen.findByText("Zuletzt geändert von admin")).toBeInTheDocument();
  });

  it("shows a pending-approval hint instead of updating when four-eyes defers the default-role change", async () => {
    // Phase 53 Session 1 (ADR 0171) - the default-role setting joined the
    // other four AD-group-mapping mutations' optional four-eyes gate.
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    getAdGroupMappingDefaultRoleMock.mockResolvedValue({
      default_role_name: null,
      updated_at: "2026-01-01T00:00:00Z",
      updated_by: null,
    });
    setAdGroupMappingDefaultRoleMock.mockResolvedValue({
      status: "pending_approval",
      config: null,
      approval_request_id: "req-1",
    });

    renderAdGroupMappings();
    await waitFor(() => expect(getAdGroupMappingDefaultRoleMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Standardrolle festlegen" });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-poststelle" } });
    fireEvent.submit(form);

    expect(
      await screen.findByText(
        "Vier-Augen-Prinzip aktiv - die Änderung wartet auf Genehmigung durch eine zweite Person, bevor sie wirksam wird."
      )
    ).toBeInTheDocument();
    // Not applied - the previous (empty) config stays displayed.
    expect(screen.queryByText(/Zuletzt geändert von/)).not.toBeInTheDocument();
  });

  it("shows an error when creating a mapping fails", async () => {
    listAdGroupMappingsMock.mockResolvedValue([]);
    listAdGroupCompositeRulesMock.mockResolvedValue([]);
    createAdGroupMappingMock.mockRejectedValue(new Error("boom"));

    renderAdGroupMappings();
    await waitFor(() => expect(listAdGroupMappingsMock).toHaveBeenCalledTimes(1));

    const form = screen.getByRole("form", { name: "Zuordnung anlegen" });
    fireEvent.change(within(form).getByLabelText("AD-Gruppenname"), { target: { value: "x" } });
    fireEvent.change(within(form).getByLabelText("Rolle"), { target: { value: "dms-poststelle" } });
    fireEvent.submit(form);

    expect(await screen.findByText("Zuordnung anlegen fehlgeschlagen")).toBeInTheDocument();
  });
});
