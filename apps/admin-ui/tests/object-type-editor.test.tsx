import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ObjectTypeEditor } from "@/components/ObjectTypeEditor";
import { I18nProvider } from "@/i18n";

function renderObjectTypeEditor() {
  return render(
    <I18nProvider>
      <ObjectTypeEditor />
    </I18nProvider>
  );
}

const listObjectTypesMock = vi.fn();
const createObjectTypeMock = vi.fn();
const updateObjectTypeMock = vi.fn();
const deleteObjectTypeMock = vi.fn();
const putObjectTypeLayoutMock = vi.fn();

vi.mock("@/lib/api", () => ({
  ROOT_PARENT_TYPE: "$ROOT",
  listObjectTypes: (...args: unknown[]) => listObjectTypesMock(...args),
  createObjectType: (...args: unknown[]) => createObjectTypeMock(...args),
  updateObjectType: (...args: unknown[]) => updateObjectTypeMock(...args),
  deleteObjectType: (...args: unknown[]) => deleteObjectTypeMock(...args),
  putObjectTypeLayout: (...args: unknown[]) => putObjectTypeLayoutMock(...args),
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
  attributes: [{ name: "Betrag", type: "decimal", required: true }],
  naming_constraints: { pattern: "{Betrag}" },
  conditions: [{ if: "Betrag > 10000", then: "require:Kostenstelle" }],
  allowed_parent_types: null,
  icon: null,
};

const PROJEKTORDNER = {
  id: 2,
  name: "Projektordner",
  applies_to: "folder",
  attributes: [],
  naming_constraints: null,
  conditions: [],
  allowed_parent_types: ["$ROOT"],
  icon: "folder-star",
};

describe("ObjectTypeEditor", () => {
  beforeEach(() => {
    listObjectTypesMock.mockReset();
    createObjectTypeMock.mockReset();
    updateObjectTypeMock.mockReset();
    deleteObjectTypeMock.mockReset();
    putObjectTypeLayoutMock.mockReset();
    listObjectTypesMock.mockResolvedValue([RECHNUNG, PROJEKTORDNER]);
  });

  it("lists existing object types", async () => {
    renderObjectTypeEditor();
    expect(await screen.findByText("Rechnung")).toBeInTheDocument();
    expect(screen.getAllByText("Projektordner").length).toBeGreaterThan(0);
  });

  it("creates an object type with a structured attribute and no custom label", async () => {
    createObjectTypeMock.mockResolvedValue({ ...RECHNUNG, id: 99 });
    renderObjectTypeEditor();
    await waitFor(() => expect(listObjectTypesMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vertrag" } });
    fireEvent.click(screen.getByText("Attribut hinzufügen"));
    fireEvent.change(screen.getByLabelText("Technischer Name"), { target: { value: "Partner" } });
    fireEvent.click(screen.getByLabelText("Pflichtfeld"));

    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp anlegen" }));

    await waitFor(() =>
      expect(createObjectTypeMock).toHaveBeenCalledWith("token-123", {
        name: "Vertrag",
        appliesTo: "document",
        attributes: [{ name: "Partner", type: "string", required: true }],
        allowedParentTypes: null,
        icon: null,
      })
    );
    // Kein abweichender Anzeigename vergeben -> kein Layout-Override nötig.
    expect(putObjectTypeLayoutMock).not.toHaveBeenCalled();
  });

  it("persists an initial smart layout for all three purposes when a display label differs", async () => {
    createObjectTypeMock.mockResolvedValue({ id: 42 });
    putObjectTypeLayoutMock.mockResolvedValue({});
    renderObjectTypeEditor();
    await waitFor(() => expect(listObjectTypesMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vertrag" } });
    fireEvent.click(screen.getByText("Attribut hinzufügen"));
    fireEvent.change(screen.getByLabelText("Technischer Name"), { target: { value: "Partner" } });
    fireEvent.change(screen.getByLabelText("Anzeigename"), { target: { value: "Vertragspartner" } });

    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp anlegen" }));

    await waitFor(() => expect(putObjectTypeLayoutMock).toHaveBeenCalledTimes(3));
    expect(putObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 42, "display", {
      rows: [{ columns: [{ attribute: "Partner", label: "Vertragspartner", required: false }] }],
      responsiveBreakpointPx: 600,
    });
    expect(putObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 42, "search", expect.anything());
    expect(putObjectTypeLayoutMock).toHaveBeenCalledWith("token-123", 42, "upload", expect.anything());
  });

  it("shows an error and does not submit when an attribute has no technical name", async () => {
    renderObjectTypeEditor();
    await waitFor(() => expect(listObjectTypesMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Kaputt" } });
    fireEvent.click(screen.getByText("Attribut hinzufügen"));
    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp anlegen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/technischen Namen/);
    expect(createObjectTypeMock).not.toHaveBeenCalled();
  });

  it("loads an existing object type into the form for editing and preserves conditions on save", async () => {
    updateObjectTypeMock.mockResolvedValue({});
    renderObjectTypeEditor();
    await screen.findByText("Rechnung");

    const row = screen.getByText("Rechnung").closest("tr")!;
    fireEvent.click(within(row).getByText("Bearbeiten"));

    expect(await screen.findByRole("form", { name: "Objekttyp speichern" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Rechnung");
    expect(screen.getByLabelText("Name")).toBeDisabled();
    // Anzeigename ist im Bearbeiten-Modus nicht editierbar (nur beim Anlegen relevant, ADR 0014).
    expect(screen.queryByLabelText("Anzeigename")).not.toBeInTheDocument();

    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp speichern" }));

    await waitFor(() =>
      expect(updateObjectTypeMock).toHaveBeenCalledWith("token-123", 1, {
        attributes: [{ name: "Betrag", type: "decimal", required: true }],
        namingConstraints: { pattern: "{Betrag}" },
        conditions: [{ if: "Betrag > 10000", then: "require:Kostenstelle" }],
        allowedParentTypes: null,
        icon: null,
      })
    );
  });

  it("shows the icon field only for folder-applying types and lists allowed-parent-type options", async () => {
    const { container } = renderObjectTypeEditor();
    await screen.findByText("Rechnung");
    expect(screen.queryByLabelText("Icon")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Gilt für"), { target: { value: "folder" } });
    expect(screen.getByLabelText("Icon")).toBeInTheDocument();

    const parentTypeGroup = container.querySelector(".checkbox-group") as HTMLElement;
    expect(within(parentTypeGroup).getByText("Direkt unter der Wurzel ($ROOT)")).toBeInTheDocument();
    expect(within(parentTypeGroup).getByText("Projektordner")).toBeInTheDocument();
  });

  it("deletes an object type", async () => {
    deleteObjectTypeMock.mockResolvedValue(undefined);
    renderObjectTypeEditor();

    await screen.findByText("Rechnung");
    const row = screen.getByText("Rechnung").closest("tr")!;
    fireEvent.click(within(row).getByText("Löschen"));

    await waitFor(() => expect(deleteObjectTypeMock).toHaveBeenCalledWith("token-123", 1));
  });
});
