import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LayoutDesigner } from "@/components/LayoutDesigner";
import { I18nProvider } from "@/i18n";

function renderLayoutDesigner() {
  return render(
    <I18nProvider>
      <LayoutDesigner />
    </I18nProvider>
  );
}

const listObjectTypesMock = vi.fn();
const getObjectTypeLayoutMock = vi.fn();
const putObjectTypeLayoutMock = vi.fn();
const resetObjectTypeLayoutMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listObjectTypes: (...args: unknown[]) => listObjectTypesMock(...args),
  getObjectTypeLayout: (...args: unknown[]) => getObjectTypeLayoutMock(...args),
  putObjectTypeLayout: (...args: unknown[]) => putObjectTypeLayoutMock(...args),
  resetObjectTypeLayout: (...args: unknown[]) => resetObjectTypeLayoutMock(...args),
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

const RECHNUNG = {
  id: 1,
  name: "Rechnung",
  applies_to: "document",
  attributes: [
    { name: "Rechnungsnummer", type: "string", required: true },
    { name: "Betrag", type: "decimal", required: true },
  ],
  naming_constraints: null,
  conditions: [],
  allowed_parent_types: null,
  icon: null,
};

const GENERATED_LAYOUT = {
  rows: [
    {
      columns: [
        { attribute: "Rechnungsnummer", label: "Rechnungsnummer", required: true },
      ],
    },
  ],
  responsive_breakpoint_px: 600,
  is_custom: false,
};

describe("LayoutDesigner", () => {
  beforeEach(() => {
    listObjectTypesMock.mockReset();
    getObjectTypeLayoutMock.mockReset();
    putObjectTypeLayoutMock.mockReset();
    resetObjectTypeLayoutMock.mockReset();
    listObjectTypesMock.mockResolvedValue([RECHNUNG]);
    getObjectTypeLayoutMock.mockResolvedValue(GENERATED_LAYOUT);
  });

  it("shows a hint when no object types exist", async () => {
    listObjectTypesMock.mockResolvedValue([]);
    renderLayoutDesigner();
    expect(await screen.findByText("Keine Objekttypen vorhanden.")).toBeInTheDocument();
  });

  it("loads the generated layout for the first object type and purpose by default", async () => {
    renderLayoutDesigner();
    await waitFor(() =>
      expect(getObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 1, "display")
    );
    expect(await screen.findByText("Automatisch generiert")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Rechnungsnummer")).toBeInTheDocument();
  });

  it("switches purpose and reloads the layout", async () => {
    renderLayoutDesigner();
    await waitFor(() => expect(getObjectTypeLayoutMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Verwendungszweck"), { target: { value: "upload" } });

    await waitFor(() =>
      expect(getObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 1, "upload")
    );
  });

  it("adds an available attribute to a row", async () => {
    renderLayoutDesigner();
    await screen.findByText("Automatisch generiert");

    const row = screen.getByText("Zeile 1").closest(".layout-row") as HTMLElement;
    fireEvent.change(within(row).getByRole("combobox"), { target: { value: "Betrag" } });
    fireEvent.click(within(row).getByText("Hinzufügen"));

    expect(within(row).getByText("Betrag")).toBeInTheDocument();
  });

  it("removes a field and its now-empty row disappears", async () => {
    renderLayoutDesigner();
    await screen.findByText("Automatisch generiert");

    fireEvent.click(screen.getByText("Entfernen"));

    expect(screen.getByText("Dieses Layout enthält aktuell keine Felder.")).toBeInTheDocument();
  });

  it("adds a new empty row via the button", async () => {
    renderLayoutDesigner();
    await screen.findByText("Automatisch generiert");

    fireEvent.click(screen.getByText("Neue Zeile hinzufügen"));

    expect(screen.getByText("Zeile 2")).toBeInTheDocument();
  });

  it("saves the current layout and shows the custom badge from the response", async () => {
    putObjectTypeLayoutMock.mockResolvedValue({ ...GENERATED_LAYOUT, is_custom: true });
    renderLayoutDesigner();
    await screen.findByText("Automatisch generiert");

    fireEvent.click(screen.getByText("Speichern"));

    await waitFor(() =>
      expect(putObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 1, "display", {
        rows: GENERATED_LAYOUT.rows,
        responsiveBreakpointPx: 600,
      })
    );
    expect(await screen.findByText("Angepasst")).toBeInTheDocument();
  });

  it("resets the layout back to the generated default", async () => {
    resetObjectTypeLayoutMock.mockResolvedValue(undefined);
    renderLayoutDesigner();
    await screen.findByText("Automatisch generiert");

    fireEvent.click(screen.getByText("Auf generiertes Layout zurücksetzen"));

    await waitFor(() =>
      expect(resetObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 1, "display")
    );
    await waitFor(() => expect(getObjectTypeLayoutMock).toHaveBeenCalledTimes(2));
  });
});
