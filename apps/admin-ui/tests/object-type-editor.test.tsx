import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
const deleteObjectTypeMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listObjectTypes: (...args: unknown[]) => listObjectTypesMock(...args),
  createObjectType: (...args: unknown[]) => createObjectTypeMock(...args),
  deleteObjectType: (...args: unknown[]) => deleteObjectTypeMock(...args),
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

describe("ObjectTypeEditor", () => {
  beforeEach(() => {
    listObjectTypesMock.mockReset();
    createObjectTypeMock.mockReset();
    deleteObjectTypeMock.mockReset();
    listObjectTypesMock.mockResolvedValue([
      {
        id: 1,
        name: "Rechnung",
        applies_to: "document",
        attributes: [{ name: "Betrag" }],
        naming_constraints: null,
        conditions: [],
      },
    ]);
  });

  it("lists existing object types", async () => {
    renderObjectTypeEditor();
    expect(await screen.findByText("Rechnung")).toBeInTheDocument();
  });

  it("creates an object type with parsed JSON attributes", async () => {
    createObjectTypeMock.mockResolvedValue({});
    renderObjectTypeEditor();
    await waitFor(() => expect(listObjectTypesMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Vertrag" } });
    fireEvent.change(screen.getByLabelText(/Attribute/), {
      target: { value: '[{"name": "Partner", "required": true}]' },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp anlegen" }));

    await waitFor(() =>
      expect(createObjectTypeMock).toHaveBeenCalledWith("token-123", {
        name: "Vertrag",
        appliesTo: "document",
        attributes: [{ name: "Partner", required: true }],
      })
    );
  });

  it("shows an error instead of submitting when attributes are invalid JSON", async () => {
    renderObjectTypeEditor();
    await waitFor(() => expect(listObjectTypesMock).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Kaputt" } });
    fireEvent.change(screen.getByLabelText(/Attribute/), { target: { value: "{not valid" } });
    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp anlegen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/gültiges JSON/);
    expect(createObjectTypeMock).not.toHaveBeenCalled();
  });

  it("deletes an object type", async () => {
    deleteObjectTypeMock.mockResolvedValue(undefined);
    renderObjectTypeEditor();

    await screen.findByText("Rechnung");
    fireEvent.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteObjectTypeMock).toHaveBeenCalledWith("token-123", 1));
  });
});
