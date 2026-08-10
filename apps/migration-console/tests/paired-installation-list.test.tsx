import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PairedInstallationList } from "@/components/PairedInstallationList";
import { I18nProvider } from "@/i18n";

const listPairedInstallationsMock = vi.fn();
const createPairedInstallationMock = vi.fn();
const deletePairedInstallationMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listPairedInstallations: (...args: unknown[]) => listPairedInstallationsMock(...args),
  createPairedInstallation: (...args: unknown[]) => createPairedInstallationMock(...args),
  deletePairedInstallation: (...args: unknown[]) => deletePairedInstallationMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
    permissions: [],
    accessToken: "token-123",
    isLoading: false,
    login: vi.fn(),
    logout: vi.fn(),
  }),
}));

function renderList() {
  return render(
    <I18nProvider>
      <PairedInstallationList />
    </I18nProvider>
  );
}

const INSTALLATION = {
  id: "install-1",
  display_name: "Filiale Nord",
  base_url: "https://nord.example.test",
  created_at: "2026-01-01T10:00:00Z",
};

describe("PairedInstallationList", () => {
  beforeEach(() => {
    listPairedInstallationsMock.mockReset();
    createPairedInstallationMock.mockReset();
    deletePairedInstallationMock.mockReset();
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("shows an empty state when no installation is paired yet", async () => {
    listPairedInstallationsMock.mockResolvedValue([]);
    renderList();

    await waitFor(() =>
      expect(screen.getByText(/Noch keine Installation gepaart/)).toBeInTheDocument()
    );
  });

  it("lists paired installations", async () => {
    listPairedInstallationsMock.mockResolvedValue([INSTALLATION]);
    renderList();

    await waitFor(() => expect(screen.getByText("Filiale Nord")).toBeInTheDocument());
    expect(screen.getByText("https://nord.example.test")).toBeInTheDocument();
  });

  it("creates a new pairing and shows the one-time API key", async () => {
    listPairedInstallationsMock.mockResolvedValue([]);
    createPairedInstallationMock.mockResolvedValue({ ...INSTALLATION, api_key: "secret-key-1" });
    renderList();

    await waitFor(() =>
      expect(screen.getByText(/Noch keine Installation gepaart/)).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: "Installation paaren" }));
    fireEvent.change(screen.getByLabelText("Anzeigename"), {
      target: { value: "Filiale Nord" },
    });
    fireEvent.change(screen.getByLabelText(/Gateway-Adresse der Ziel-Installation/), {
      target: { value: "https://nord.example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Paaren" }));

    await waitFor(() =>
      expect(createPairedInstallationMock).toHaveBeenCalledWith("token-123", {
        displayName: "Filiale Nord",
        baseUrl: "https://nord.example.test",
        apiKey: undefined,
      })
    );
    expect(screen.getByText("secret-key-1")).toBeInTheDocument();
  });

  it("deletes a pairing after confirmation", async () => {
    listPairedInstallationsMock.mockResolvedValue([INSTALLATION]);
    deletePairedInstallationMock.mockResolvedValue(undefined);
    renderList();

    await waitFor(() => expect(screen.getByText("Filiale Nord")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Löschen" }));

    await waitFor(() =>
      expect(deletePairedInstallationMock).toHaveBeenCalledWith("token-123", "install-1")
    );
  });
});
