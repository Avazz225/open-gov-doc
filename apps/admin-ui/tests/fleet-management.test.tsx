import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FleetManagementView } from "@/components/FleetManagementView";
import { I18nProvider } from "@/i18n";

function renderView() {
  return render(
    <I18nProvider>
      <FleetManagementView />
    </I18nProvider>
  );
}

const listManagedInstallationsMock = vi.fn();
const createManagedInstallationMock = vi.fn();
const deleteManagedInstallationMock = vi.fn();
const getManagedInstallationsStatusMock = vi.fn();
const pushInstallationLicenseMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listManagedInstallations: (...args: unknown[]) => listManagedInstallationsMock(...args),
  createManagedInstallation: (...args: unknown[]) => createManagedInstallationMock(...args),
  deleteManagedInstallation: (...args: unknown[]) => deleteManagedInstallationMock(...args),
  getManagedInstallationsStatus: (...args: unknown[]) => getManagedInstallationsStatusMock(...args),
  pushInstallationLicense: (...args: unknown[]) => pushInstallationLicenseMock(...args),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

const INSTALLATION_A = {
  id: "inst-a",
  display_name: "Standort A",
  gateway_base_url: "https://a.example.com",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

async function loadWithKey(key = "test-operator-key") {
  renderView();
  fireEvent.change(screen.getByPlaceholderText("Erforderlich für jede Aktion auf dieser Seite"), {
    target: { value: key },
  });
  fireEvent.click(screen.getByRole("button", { name: "Laden" }));
}

describe("FleetManagementView", () => {
  beforeEach(() => {
    listManagedInstallationsMock.mockReset();
    createManagedInstallationMock.mockReset();
    deleteManagedInstallationMock.mockReset();
    getManagedInstallationsStatusMock.mockReset();
    pushInstallationLicenseMock.mockReset();
  });

  it("does not list installations before an operator key is loaded", () => {
    renderView();
    expect(listManagedInstallationsMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Standort A")).not.toBeInTheDocument();
  });

  it("loads and lists managed installations once the operator key is submitted", async () => {
    listManagedInstallationsMock.mockResolvedValue([INSTALLATION_A]);

    await loadWithKey();

    expect(await screen.findByText("Standort A")).toBeInTheDocument();
    expect(listManagedInstallationsMock).toHaveBeenCalledWith("test-operator-key");
  });

  it("shows an unreachable state when the key is wrong or the service is down", async () => {
    listManagedInstallationsMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await loadWithKey();

    expect(
      await screen.findByText("Fleet Management Service nicht erreichbar oder Schlüssel ungültig.")
    ).toBeInTheDocument();
  });

  it("registers a new installation and shows the one-time plaintext key", async () => {
    listManagedInstallationsMock.mockResolvedValue([]);
    createManagedInstallationMock.mockResolvedValue({
      ...INSTALLATION_A,
      fleet_agent_api_key: "plaintext-key-shown-once",
    });

    await loadWithKey();
    await screen.findByText("Neue Installation registrieren");

    const [nameInput, urlInput] = screen.getAllByRole("textbox");
    fireEvent.change(nameInput, { target: { value: "Standort A" } });
    fireEvent.change(urlInput, { target: { value: "https://a.example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Registrieren" }));

    await screen.findByText("plaintext-key-shown-once");
    expect(createManagedInstallationMock).toHaveBeenCalledWith(
      { display_name: "Standort A", gateway_base_url: "https://a.example.com" },
      "test-operator-key"
    );
  });

  it("deletes an installation and reloads the list", async () => {
    listManagedInstallationsMock
      .mockResolvedValueOnce([INSTALLATION_A])
      .mockResolvedValueOnce([]);
    deleteManagedInstallationMock.mockResolvedValue(undefined);

    await loadWithKey();
    await screen.findByText("Standort A");

    fireEvent.click(screen.getByRole("button", { name: "Entfernen" }));

    expect(deleteManagedInstallationMock).toHaveBeenCalledWith("inst-a", "test-operator-key");
    await vi.waitFor(() => expect(screen.queryByText("Standort A")).not.toBeInTheDocument());
  });

  it("checks status of all installations", async () => {
    listManagedInstallationsMock.mockResolvedValue([INSTALLATION_A]);
    getManagedInstallationsStatusMock.mockResolvedValue([
      { id: "inst-a", display_name: "Standort A", reachable: true },
    ]);

    await loadWithKey();
    await screen.findByText("Standort A");

    fireEvent.click(screen.getByRole("button", { name: "Status aller Installationen prüfen" }));

    expect(await screen.findByText("Erreichbar")).toBeInTheDocument();
    expect(getManagedInstallationsStatusMock).toHaveBeenCalledWith("test-operator-key");
  });

  it("pushes a license token to an installation", async () => {
    listManagedInstallationsMock.mockResolvedValue([INSTALLATION_A]);
    pushInstallationLicenseMock.mockResolvedValue({ status: "ok" });

    await loadWithKey();
    await screen.findByText("Standort A");

    fireEvent.change(screen.getByPlaceholderText("Lizenz-Token"), {
      target: { value: "license-token-xyz" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Übertragen" }));

    expect(pushInstallationLicenseMock).toHaveBeenCalledWith(
      "inst-a",
      "license-token-xyz",
      "test-operator-key"
    );
  });
});
