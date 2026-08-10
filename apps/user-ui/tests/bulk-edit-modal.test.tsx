import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BulkEditModal, type BulkEditItem } from "@/components/BulkEditModal";
import { I18nProvider } from "@/i18n";
import { ApiError } from "@/lib/api";

const getObjectTypeMock = vi.fn();
const getObjectTypeLayoutMock = vi.fn();
const updateDocumentMetadataMock = vi.fn();
const updateFolderAttributesMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getObjectType: (...args: unknown[]) => getObjectTypeMock(...args),
    getObjectTypeLayout: (...args: unknown[]) => getObjectTypeLayoutMock(...args),
    updateDocumentMetadata: (...args: unknown[]) => updateDocumentMetadataMock(...args),
    updateFolderAttributes: (...args: unknown[]) => updateFolderAttributesMock(...args),
  };
});

const OBJECT_TYPE = {
  id: 5,
  name: "Vertrag",
  applies_to: "document",
  attributes: [{ name: "Betrag", type: "string" }],
  icon: null,
};

const LAYOUT = {
  rows: [{ columns: [{ attribute: "Betrag", label: "Betrag", required: false }] }],
  responsive_breakpoint_px: 600,
  is_custom: false,
};

const DOC_A: BulkEditItem = {
  kind: "document",
  id: "doc-a",
  name: "Rechnung A",
  object_type_id: 5,
  attributes: { Betrag: "100", Sonstiges: "bleibt" },
};

const DOC_B: BulkEditItem = {
  kind: "document",
  id: "doc-b",
  name: "Rechnung B",
  object_type_id: 5,
  attributes: { Betrag: "200" },
};

function renderModal(items: BulkEditItem[], onClose = vi.fn(), onDone = vi.fn()) {
  return render(
    <I18nProvider>
      <BulkEditModal token="token-123" items={items} onClose={onClose} onDone={onDone} />
    </I18nProvider>
  );
}

describe("BulkEditModal", () => {
  beforeEach(() => {
    getObjectTypeMock.mockReset();
    getObjectTypeLayoutMock.mockReset();
    updateDocumentMetadataMock.mockReset();
    updateFolderAttributesMock.mockReset();
    getObjectTypeMock.mockResolvedValue(OBJECT_TYPE);
    getObjectTypeLayoutMock.mockResolvedValue(LAYOUT);
  });

  it("shows an explanatory message for a selection with mixed object types", async () => {
    renderModal([DOC_A, { ...DOC_B, object_type_id: 9 }]);

    expect(
      await screen.findByText(
        "Nur Dokumente ODER nur Ordner desselben Objekttyps können gemeinsam bearbeitet werden."
      )
    ).toBeInTheDocument();
    expect(getObjectTypeMock).not.toHaveBeenCalled();
  });

  it("shows an explanatory message for a selection mixing documents and folders", async () => {
    renderModal([DOC_A, { ...DOC_A, id: "folder-a", kind: "folder" }]);

    expect(
      await screen.findByText(
        "Nur Dokumente ODER nur Ordner desselben Objekttyps können gemeinsam bearbeitet werden."
      )
    ).toBeInTheDocument();
  });

  it("shows a message when the selection has no object type", async () => {
    renderModal([{ ...DOC_A, object_type_id: null }]);

    expect(
      await screen.findByText(
        "Die ausgewählten Objekte haben keinen Objekttyp - keine gemeinsamen Attribute zum Bearbeiten."
      )
    ).toBeInTheDocument();
  });

  it("loads the shared object type/layout for a homogeneous selection", async () => {
    renderModal([DOC_A, DOC_B]);

    await waitFor(() => expect(getObjectTypeMock).toHaveBeenCalledWith("token-123", 5));
    expect(await screen.findByLabelText("Betrag")).toBeInTheDocument();
  });

  it("only applies filled fields, merging into each object's own existing attributes", async () => {
    updateDocumentMetadataMock.mockResolvedValue({});
    const onDone = vi.fn();
    renderModal([DOC_A, DOC_B], vi.fn(), onDone);

    await screen.findByLabelText("Betrag");
    fireEvent.change(screen.getByLabelText("Betrag"), { target: { value: "999" } });
    fireEvent.click(screen.getByText("Übernehmen"));

    await waitFor(() => expect(updateDocumentMetadataMock).toHaveBeenCalledTimes(2));
    expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "doc-a", {
      attributes: { Betrag: "999", Sonstiges: "bleibt" },
    });
    expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "doc-b", {
      attributes: { Betrag: "999" },
    });
    expect(onDone).toHaveBeenCalled();
  });

  it("leaves attributes untouched when the field is left blank", async () => {
    updateDocumentMetadataMock.mockResolvedValue({});
    renderModal([DOC_A]);

    await screen.findByLabelText("Betrag");
    fireEvent.click(screen.getByText("Übernehmen"));

    await waitFor(() => expect(updateDocumentMetadataMock).toHaveBeenCalledTimes(1));
    expect(updateDocumentMetadataMock).toHaveBeenCalledWith("token-123", "doc-a", {
      attributes: { Betrag: "100", Sonstiges: "bleibt" },
    });
  });

  it("shows a per-object success/failure summary, not all-or-nothing", async () => {
    updateDocumentMetadataMock.mockImplementation(async (_token: string, id: string) => {
      if (id === "doc-a") return {};
      throw new ApiError(400, '{"errors":["Betrag muss numerisch sein"]}');
    });
    renderModal([DOC_A, DOC_B]);

    await screen.findByLabelText("Betrag");
    fireEvent.click(screen.getByText("Übernehmen"));

    expect(await screen.findByText("Rechnung A")).toBeInTheDocument();
    expect(screen.getByText("Rechnung B")).toBeInTheDocument();
    expect(screen.getByText("Erfolgreich")).toBeInTheDocument();
    expect(screen.getByText("Fehlgeschlagen")).toBeInTheDocument();
  });
});
