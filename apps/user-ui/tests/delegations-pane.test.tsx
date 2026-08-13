import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DelegationsPane } from "@/components/DelegationsPane";
import { I18nProvider } from "@/i18n";

const listMyDelegationsMock = vi.fn();
const listActiveDelegationsForDeputyMock = vi.fn();
const createDelegationMock = vi.fn();
const revokeDelegationMock = vi.fn();
const lookupUserByUsernameMock = vi.fn();
const lookupUserByIdMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listMyDelegations: (...args: unknown[]) => listMyDelegationsMock(...args),
    listActiveDelegationsForDeputy: (...args: unknown[]) =>
      listActiveDelegationsForDeputyMock(...args),
    createDelegation: (...args: unknown[]) => createDelegationMock(...args),
    revokeDelegation: (...args: unknown[]) => revokeDelegationMock(...args),
    lookupUserByUsername: (...args: unknown[]) => lookupUserByUsernameMock(...args),
    lookupUserById: (...args: unknown[]) => lookupUserByIdMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <DelegationsPane token="token-123" currentPrincipalId="alice-sub" />
    </I18nProvider>
  );
}

const farFutureStart = new Date(Date.now() - 3600_000).toISOString();
const farFutureEnd = new Date(Date.now() + 86400_000).toISOString();

describe("DelegationsPane", () => {
  beforeEach(() => {
    listMyDelegationsMock.mockReset();
    listActiveDelegationsForDeputyMock.mockReset();
    createDelegationMock.mockReset();
    revokeDelegationMock.mockReset();
    lookupUserByUsernameMock.mockReset();
    lookupUserByIdMock.mockReset();

    listMyDelegationsMock.mockResolvedValue([]);
    listActiveDelegationsForDeputyMock.mockResolvedValue([]);
    // Standardmäßig nicht auflösbar (P19-S4) - `usePrincipalNames` fällt in
    // diesem Fall auf die rohe principal_id zurück, bestehende Tests bleiben
    // dadurch unverändert gültig; ein dedizierter Test unten prüft die
    // tatsächliche Namensauflösung.
    lookupUserByIdMock.mockRejectedValue(new Error("not mocked"));
  });

  it("shows empty states when no delegations exist in either direction", async () => {
    renderPane();

    expect(await screen.findByText("Noch keine Stellvertretung hinterlegt.")).toBeInTheDocument();
    expect(screen.getByText("Du vertrittst aktuell niemanden.")).toBeInTheDocument();
  });

  it("lists an active delegation I created with a revoke button", async () => {
    listMyDelegationsMock.mockResolvedValue([
      {
        id: "d1",
        delegator_principal_id: "alice-sub",
        deputy_principal_id: "bob-sub",
        starts_at: farFutureStart,
        ends_at: farFutureEnd,
        scope_object_type_ids: null,
        scope_process_definition_ids: null,
        scope_folder_resource_ids: null,
        created_at: farFutureStart,
        revoked_at: null,
        revoked_by: null,
      },
    ]);

    renderPane();

    expect(await screen.findByText(/bob-sub/)).toBeInTheDocument();
    expect(screen.getByText("Widerrufen")).toBeInTheDocument();
  });

  it("lists who I am currently a deputy for, read-only", async () => {
    listActiveDelegationsForDeputyMock.mockResolvedValue([
      {
        id: "d2",
        delegator_principal_id: "carol-sub",
        deputy_principal_id: "alice-sub",
        starts_at: farFutureStart,
        ends_at: farFutureEnd,
        scope_object_type_ids: null,
        scope_process_definition_ids: null,
        scope_folder_resource_ids: null,
        created_at: farFutureStart,
        revoked_at: null,
        revoked_by: null,
      },
    ]);

    renderPane();

    expect(await screen.findByText(/carol-sub/)).toBeInTheDocument();
    expect(screen.queryByText("Widerrufen")).not.toBeInTheDocument();
  });

  it("creates a new delegation by resolving the deputy username first", async () => {
    lookupUserByUsernameMock.mockResolvedValue({ id: "bob-sub", username: "bob" });
    createDelegationMock.mockResolvedValue({
      id: "d1",
      delegator_principal_id: "alice-sub",
      deputy_principal_id: "bob-sub",
      starts_at: farFutureStart,
      ends_at: farFutureEnd,
      scope_object_type_ids: null,
      scope_process_definition_ids: null,
      scope_folder_resource_ids: null,
      created_at: farFutureStart,
      revoked_at: null,
      revoked_by: null,
    });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Noch keine Stellvertretung hinterlegt.");
    await user.type(
      screen.getByPlaceholderText("Nutzername der Stellvertretung"),
      "bob"
    );
    await user.click(screen.getByText("Hinterlegen"));

    await waitFor(() => expect(lookupUserByUsernameMock).toHaveBeenCalledWith("token-123", "bob"));
    await waitFor(() => expect(createDelegationMock).toHaveBeenCalled());
    expect(createDelegationMock.mock.calls[0][0]).toBe("token-123");
    expect(createDelegationMock.mock.calls[0][1].deputyPrincipalId).toBe("bob-sub");
  });

  it("shows an error when creating with an unknown deputy username", async () => {
    lookupUserByUsernameMock.mockRejectedValue(new Error("404"));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Noch keine Stellvertretung hinterlegt.");
    await user.type(screen.getByPlaceholderText("Nutzername der Stellvertretung"), "ghost");
    await user.click(screen.getByText("Hinterlegen"));

    expect(
      await screen.findByText("Anlegen fehlgeschlagen - Nutzername unbekannt oder ungültiger Zeitraum")
    ).toBeInTheDocument();
    expect(createDelegationMock).not.toHaveBeenCalled();
  });

  it("revokes a delegation", async () => {
    listMyDelegationsMock
      .mockResolvedValueOnce([
        {
          id: "d1",
          delegator_principal_id: "alice-sub",
          deputy_principal_id: "bob-sub",
          starts_at: farFutureStart,
          ends_at: farFutureEnd,
          scope_object_type_ids: null,
          scope_process_definition_ids: null,
          scope_folder_resource_ids: null,
          created_at: farFutureStart,
          revoked_at: null,
          revoked_by: null,
        },
      ])
      .mockResolvedValueOnce([]);
    revokeDelegationMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/bob-sub/);
    await user.click(screen.getByText("Widerrufen"));

    await waitFor(() => expect(revokeDelegationMock).toHaveBeenCalledWith("token-123", "d1"));
  });

  it("resolves a raw principal_id to a username (P19-S4)", async () => {
    listMyDelegationsMock.mockResolvedValue([
      {
        id: "d1",
        delegator_principal_id: "alice-sub",
        deputy_principal_id: "bob-sub",
        starts_at: farFutureStart,
        ends_at: farFutureEnd,
        scope_object_type_ids: null,
        scope_process_definition_ids: null,
        scope_folder_resource_ids: null,
        created_at: farFutureStart,
        revoked_at: null,
        revoked_by: null,
      },
    ]);
    lookupUserByIdMock.mockResolvedValue({ id: "bob-sub", username: "bob" });

    renderPane();

    expect(await screen.findByText(/^bob —/)).toBeInTheDocument();
    expect(screen.queryByText(/bob-sub/)).not.toBeInTheDocument();
  });
});
