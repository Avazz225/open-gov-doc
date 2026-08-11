import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KontaktePane } from "@/components/KontaktePane";
import { I18nProvider } from "@/i18n";

const searchDirectoryMock = vi.fn();
const getDirectoryFederationStatusMock = vi.fn();
const searchFederatedDirectoryMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    searchDirectory: (...args: unknown[]) => searchDirectoryMock(...args),
    getDirectoryFederationStatus: (...args: unknown[]) => getDirectoryFederationStatusMock(...args),
    searchFederatedDirectory: (...args: unknown[]) => searchFederatedDirectoryMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <KontaktePane token="token-123" />
    </I18nProvider>
  );
}

describe("KontaktePane", () => {
  beforeEach(() => {
    searchDirectoryMock.mockReset();
    getDirectoryFederationStatusMock.mockReset();
    searchFederatedDirectoryMock.mockReset();
    getDirectoryFederationStatusMock.mockResolvedValue({
      enabled: false,
      peer_installation_count: 0,
    });
  });

  it("shows nothing before a search is run", async () => {
    renderPane();
    await waitFor(() => expect(getDirectoryFederationStatusMock).toHaveBeenCalled());
    expect(screen.queryByText("Keine Treffer.")).not.toBeInTheDocument();
  });

  it("searches and shows local results", async () => {
    searchDirectoryMock.mockResolvedValue([
      { id: "u1", username: "amustermann", email: "a@example.com", first_name: "Anna", last_name: "Mustermann" },
    ]);

    const user = userEvent.setup();
    renderPane();

    await user.type(screen.getByLabelText("Suche"), "Mustermann");
    await user.click(screen.getByText("Suchen"));

    expect(await screen.findByText(/Anna Mustermann/)).toBeInTheDocument();
    expect(searchDirectoryMock).toHaveBeenCalledWith("token-123", "Mustermann");
  });

  it("shows the empty state when there are no results", async () => {
    searchDirectoryMock.mockResolvedValue([]);

    const user = userEvent.setup();
    renderPane();

    await user.type(screen.getByLabelText("Suche"), "niemand");
    await user.click(screen.getByText("Suchen"));

    expect(await screen.findByText("Keine Treffer.")).toBeInTheDocument();
  });

  it("does not show the federated checkbox when federation is disabled", async () => {
    renderPane();
    await waitFor(() => expect(getDirectoryFederationStatusMock).toHaveBeenCalled());
    expect(screen.queryByText("Auch föderierte Installationen durchsuchen")).not.toBeInTheDocument();
  });

  it("shows federated results when enabled and the checkbox is checked", async () => {
    getDirectoryFederationStatusMock.mockResolvedValue({
      enabled: true,
      peer_installation_count: 1,
    });
    searchDirectoryMock.mockResolvedValue([]);
    searchFederatedDirectoryMock.mockResolvedValue([
      {
        id: "u2",
        username: "bfremder",
        email: "b@peer.example.com",
        first_name: "Bea",
        last_name: "Fremder",
        installation_id: "peer-1",
        installation_display_name: "Andere Installation (Kontakte)",
      },
    ]);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Auch föderierte Installationen durchsuchen");
    await user.click(screen.getByText("Auch föderierte Installationen durchsuchen"));
    await user.type(screen.getByLabelText("Suche"), "Fremder");
    await user.click(screen.getByText("Suchen"));

    expect(await screen.findByText(/Bea Fremder/)).toBeInTheDocument();
    expect(searchFederatedDirectoryMock).toHaveBeenCalledWith("token-123", "Fremder");
  });

  it("shows an error message when the search fails", async () => {
    searchDirectoryMock.mockRejectedValue(new Error("boom"));

    const user = userEvent.setup();
    renderPane();

    await user.type(screen.getByLabelText("Suche"), "x");
    await user.click(screen.getByText("Suchen"));

    expect(await screen.findByText("Suche fehlgeschlagen")).toBeInTheDocument();
  });
});
