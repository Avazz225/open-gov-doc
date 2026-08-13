import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TeamspacesPane } from "@/components/TeamspacesPane";
import { I18nProvider } from "@/i18n";

const listTeamspacesMock = vi.fn();
const createTeamspaceMock = vi.fn();
const deleteTeamspaceMock = vi.fn();
const listTeamspaceMembersMock = vi.fn();
const inviteTeamspaceMemberMock = vi.fn();
const removeTeamspaceMemberMock = vi.fn();
const listTeamspaceAppointmentsMock = vi.fn();
const createTeamspaceAppointmentMock = vi.fn();
const deleteTeamspaceAppointmentMock = vi.fn();
const listTeamspaceContactsMock = vi.fn();
const createTeamspaceContactMock = vi.fn();
const deleteTeamspaceContactMock = vi.fn();
const lookupUserByUsernameMock = vi.fn();
const lookupUserByIdMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listTeamspaces: (...args: unknown[]) => listTeamspacesMock(...args),
    createTeamspace: (...args: unknown[]) => createTeamspaceMock(...args),
    deleteTeamspace: (...args: unknown[]) => deleteTeamspaceMock(...args),
    listTeamspaceMembers: (...args: unknown[]) => listTeamspaceMembersMock(...args),
    inviteTeamspaceMember: (...args: unknown[]) => inviteTeamspaceMemberMock(...args),
    removeTeamspaceMember: (...args: unknown[]) => removeTeamspaceMemberMock(...args),
    listTeamspaceAppointments: (...args: unknown[]) => listTeamspaceAppointmentsMock(...args),
    createTeamspaceAppointment: (...args: unknown[]) => createTeamspaceAppointmentMock(...args),
    deleteTeamspaceAppointment: (...args: unknown[]) => deleteTeamspaceAppointmentMock(...args),
    listTeamspaceContacts: (...args: unknown[]) => listTeamspaceContactsMock(...args),
    createTeamspaceContact: (...args: unknown[]) => createTeamspaceContactMock(...args),
    deleteTeamspaceContact: (...args: unknown[]) => deleteTeamspaceContactMock(...args),
    lookupUserByUsername: (...args: unknown[]) => lookupUserByUsernameMock(...args),
    lookupUserById: (...args: unknown[]) => lookupUserByIdMock(...args),
  };
});

const TEAMSPACE = {
  id: "ts-1",
  name: "Projekt X",
  description: "Testbeschreibung",
  root_folder_id: "folder-1",
  created_by: "alice",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const ALICE_MEMBER = {
  id: 1,
  teamspace_id: "ts-1",
  principal_id: "alice-sub",
  can_manage_members: true,
  invited_by: "alice-sub",
  invited_at: "2026-01-01T00:00:00Z",
};

function renderPane(onOpenFolder = vi.fn()) {
  return render(
    <I18nProvider>
      <TeamspacesPane token="token-123" currentPrincipalId="alice-sub" onOpenFolder={onOpenFolder} />
    </I18nProvider>
  );
}

describe("TeamspacesPane", () => {
  beforeEach(() => {
    listTeamspacesMock.mockReset();
    createTeamspaceMock.mockReset();
    deleteTeamspaceMock.mockReset();
    listTeamspaceMembersMock.mockReset();
    inviteTeamspaceMemberMock.mockReset();
    removeTeamspaceMemberMock.mockReset();
    listTeamspaceAppointmentsMock.mockReset();
    createTeamspaceAppointmentMock.mockReset();
    deleteTeamspaceAppointmentMock.mockReset();
    listTeamspaceContactsMock.mockReset();
    createTeamspaceContactMock.mockReset();
    deleteTeamspaceContactMock.mockReset();
    lookupUserByUsernameMock.mockReset();
    lookupUserByIdMock.mockReset();

    listTeamspaceMembersMock.mockResolvedValue([ALICE_MEMBER]);
    listTeamspaceAppointmentsMock.mockResolvedValue([]);
    listTeamspaceContactsMock.mockResolvedValue([]);
    // Standardmäßig nicht auflösbar (P19-S4) - `usePrincipalNames` fällt in
    // diesem Fall auf die rohe principal_id zurück, bestehende Tests bleiben
    // dadurch unverändert gültig; ein dedizierter Test unten prüft die
    // tatsächliche Namensauflösung.
    lookupUserByIdMock.mockRejectedValue(new Error("not mocked"));
  });

  it("shows an empty-state message when there are no teamspaces", async () => {
    listTeamspacesMock.mockResolvedValue([]);

    renderPane();

    expect(
      await screen.findByText("Noch keine Team-Arbeitsbereiche - lege unten den ersten an.")
    ).toBeInTheDocument();
  });

  it("lists existing teamspaces", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);

    renderPane();

    expect(await screen.findByText("Projekt X")).toBeInTheDocument();
  });

  it("creates a new teamspace via the form", async () => {
    listTeamspacesMock.mockResolvedValueOnce([]).mockResolvedValueOnce([TEAMSPACE]);
    createTeamspaceMock.mockResolvedValue(TEAMSPACE);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Noch keine Team-Arbeitsbereiche - lege unten den ersten an.");
    await user.type(screen.getByPlaceholderText("Name"), "Projekt X");
    await user.click(screen.getByText("Neu anlegen"));

    await waitFor(() =>
      expect(createTeamspaceMock).toHaveBeenCalledWith("token-123", {
        name: "Projekt X",
        description: "",
      })
    );
  });

  it("selecting a teamspace loads members/appointments/contacts", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));

    await waitFor(() => expect(listTeamspaceMembersMock).toHaveBeenCalledWith("token-123", "ts-1"));
    expect(await screen.findByText("alice-sub (Verwaltung)")).toBeInTheDocument();
  });

  it("calls onOpenFolder with the teamspace root folder id", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);
    const onOpenFolder = vi.fn();

    const user = userEvent.setup();
    renderPane(onOpenFolder);

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));
    await screen.findByText("Ordner öffnen");
    await user.click(screen.getByText("Ordner öffnen"));

    expect(onOpenFolder).toHaveBeenCalledWith("folder-1");
  });

  it("invites a member by resolving the username first", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);
    lookupUserByUsernameMock.mockResolvedValue({ id: "bob-sub", username: "bob" });
    inviteTeamspaceMemberMock.mockResolvedValue({
      id: 2,
      teamspace_id: "ts-1",
      principal_id: "bob-sub",
      can_manage_members: false,
      invited_by: "alice-sub",
      invited_at: "2026-01-02T00:00:00Z",
    });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));
    await screen.findByPlaceholderText("Nutzername");
    await user.type(screen.getByPlaceholderText("Nutzername"), "bob");
    await user.click(screen.getByText("Einladen"));

    await waitFor(() => expect(lookupUserByUsernameMock).toHaveBeenCalledWith("token-123", "bob"));
    await waitFor(() =>
      expect(inviteTeamspaceMemberMock).toHaveBeenCalledWith("token-123", "ts-1", {
        principalId: "bob-sub",
      })
    );
  });

  it("shows an error when inviting an unknown username", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);
    lookupUserByUsernameMock.mockRejectedValue(new Error("404"));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));
    await screen.findByPlaceholderText("Nutzername");
    await user.type(screen.getByPlaceholderText("Nutzername"), "ghost");
    await user.click(screen.getByText("Einladen"));

    expect(
      await screen.findByText("Einladen fehlgeschlagen - Nutzername unbekannt oder bereits Mitglied")
    ).toBeInTheDocument();
    expect(inviteTeamspaceMemberMock).not.toHaveBeenCalled();
  });

  it("deletes the teamspace when the manager confirms", async () => {
    listTeamspacesMock
      .mockResolvedValueOnce([TEAMSPACE])
      .mockResolvedValueOnce([]);
    deleteTeamspaceMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));
    await screen.findByText("Team-Arbeitsbereich löschen");
    await user.click(screen.getByText("Team-Arbeitsbereich löschen"));

    await waitFor(() => expect(deleteTeamspaceMock).toHaveBeenCalledWith("token-123", "ts-1"));
  });

  it("hides management actions for a non-manager member", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);
    listTeamspaceMembersMock.mockResolvedValue([
      { ...ALICE_MEMBER, principal_id: "bob-sub", can_manage_members: false },
    ]);

    const user = userEvent.setup();
    render(
      <I18nProvider>
        <TeamspacesPane token="token-123" currentPrincipalId="bob-sub" onOpenFolder={vi.fn()} />
      </I18nProvider>
    );

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));
    await screen.findByText("Ordner öffnen");

    expect(screen.queryByText("Team-Arbeitsbereich löschen")).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Nutzername")).not.toBeInTheDocument();
  });

  it("resolves a raw principal_id to a username (P19-S4)", async () => {
    listTeamspacesMock.mockResolvedValue([TEAMSPACE]);
    lookupUserByIdMock.mockResolvedValue({ id: "alice-sub", username: "alice" });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Projekt X");
    await user.click(screen.getByText("Öffnen"));

    expect(await screen.findByText("alice (Verwaltung)")).toBeInTheDocument();
    expect(screen.queryByText("alice-sub (Verwaltung)")).not.toBeInTheDocument();
  });
});
