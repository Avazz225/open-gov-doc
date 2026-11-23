import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConformityReportPanel } from "@/components/structure/ConformityReportPanel";
import { StructureSettings } from "@/components/structure/StructureSettings";
import { TemplateManager } from "@/components/structure/TemplateManager";
import { ObjectTypeEditor } from "@/components/ObjectTypeEditor";
import { I18nProvider } from "@/i18n";

// P91-S3 (ADR 0255): the "Ablagestruktur" page and the folder-class fields.

const api = vi.hoisted(() => ({
  listObjectTypes: vi.fn(),
  getStructureConfig: vi.fn(),
  putStructureConfig: vi.fn(),
  getConformityReport: vi.fn(),
  retypeObjects: vi.fn(),
  listFolderTemplates: vi.fn(),
  getFolderTemplate: vi.fn(),
  createFolderTemplate: vi.fn(),
  updateFolderTemplate: vi.fn(),
  deleteFolderTemplate: vi.fn(),
  listChildFolders: vi.fn(),
  createObjectType: vi.fn(),
  updateObjectType: vi.fn(),
  deleteObjectType: vi.fn(),
  putObjectTypeLayout: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ...api,
  ROOT_PARENT_TYPE: "$ROOT",
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
  return { ...actual, useAuth: () => ({ accessToken: "token-123", permissions: [], isLoading: false }) };
});

const TYPES = [
  { id: 1, name: "Abteilung", applies_to: "folder", attributes: [], allowed_parent_types: ["$ROOT"], allowed_child_folder_types: ["Team"] },
  { id: 2, name: "Team", applies_to: "folder", attributes: [], allowed_parent_types: null },
  { id: 3, name: "Akte", applies_to: "document", attributes: [], allowed_parent_types: null },
];
const CONFIG = {
  root_allowed_folder_types: null,
  root_allowed_document_types: null,
  enforcement: "off",
  updated_at: "2026-11-23T00:00:00Z",
};

function wrap(node: React.ReactNode) {
  return render(<I18nProvider>{node}</I18nProvider>);
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.listObjectTypes.mockResolvedValue(TYPES);
  api.getStructureConfig.mockResolvedValue(CONFIG);
});

describe("StructureSettings", () => {
  it("saves the mode and the root's folder list and draws the overview", async () => {
    api.putStructureConfig.mockImplementation(async (_t, body) => ({ ...CONFIG, ...body }));
    wrap(<StructureSettings />);
    fireEvent.click(await screen.findByLabelText(/^Nur melden/));
    const rootFolders = screen.getByRole("group", { name: "Ordner auf oberster Ebene" });
    fireEvent.click(within(rootFolders).getByLabelText("Nur ausgewählte"));
    fireEvent.click(within(rootFolders).getByLabelText("Abteilung"));

    const overview = screen.getByRole("list", { name: "Übersicht" });
    expect(overview).toHaveTextContent("Abteilung");
    expect(overview).toHaveTextContent("Team");

    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() =>
      expect(api.putStructureConfig).toHaveBeenCalledWith("token-123", {
        enforcement: "report",
        root_allowed_folder_types: ["Abteilung"],
        root_allowed_document_types: null,
      })
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Gespeichert.");
  });
});

describe("ConformityReportPanel", () => {
  it("lists deviations and retypes the selection", async () => {
    api.getConformityReport.mockResolvedValue({
      enforcement: "report",
      checked: 10,
      nonconforming: 1,
      counts: { placement: 1, missing_required: 0, unknown_type: 0 },
      truncated: false,
      items: [
        {
          id: "f1",
          path: "/Bauamt/Lose",
          name: "Lose",
          object_type: null,
          problems: [{ kind: "placement", messages: ["Unter „Abteilung“ sind nur Ordner dieser Klassen erlaubt: Team"] }],
        },
      ],
    });
    api.retypeObjects.mockResolvedValue({ updated: ["f1"], failed: [] });
    wrap(<ConformityReportPanel kind="folders" />);
    expect(await screen.findByText("/Bauamt/Lose")).toBeInTheDocument();
    expect(screen.getByText(/10 geprüft, 1 weichen ab/)).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("„/Bauamt/Lose“ auswählen"));
    fireEvent.change(screen.getByLabelText("Typ zuordnen"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "1 ausgewählte zuordnen" }));
    await waitFor(() => expect(api.retypeObjects).toHaveBeenCalledWith("token-123", "folders", ["f1"], 2));
    expect(await screen.findByRole("status", { name: "Ergebnis der Zuordnung" })).toHaveTextContent(
      "1 zugeordnet, 0 abgelehnt."
    );
  });
});

describe("TemplateManager", () => {
  it("builds a new template node by node and shows the server's refusal", async () => {
    api.listFolderTemplates.mockResolvedValue([]);
    api.createFolderTemplate.mockRejectedValueOnce(
      new (await import("@/lib/api")).ApiError(422, "/Abteilung/Lose: Unter „Abteilung“ sind nur Ordner dieser Klassen erlaubt: Team")
    );
    api.createFolderTemplate.mockResolvedValueOnce({ id: "t1", name: "Gerüst" });
    wrap(<TemplateManager />);
    fireEvent.click(await screen.findByRole("button", { name: "Neue Vorlage" }));
    const form = screen.getByRole("form", { name: "Vorlage bearbeiten" });
    fireEvent.change(within(form).getByLabelText("Name der Vorlage"), { target: { value: "Gerüst" } });
    const root = within(form).getByRole("listitem", { name: "Oberster Ordner" });
    fireEvent.change(within(root).getAllByLabelText("Ordnername")[0], { target: { value: "Abteilung" } });
    fireEvent.change(within(root).getAllByLabelText("Ordnerklasse")[0], { target: { value: "1" } });
    fireEvent.click(within(root).getAllByRole("button", { name: "Unterordner hinzufügen" })[0]);
    const child = within(form).getByRole("listitem", { name: "Ordner 1" });
    fireEvent.change(within(child).getByLabelText("Ordnername"), { target: { value: "Lose" } });

    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("/Abteilung/Lose");
    expect(api.createFolderTemplate).toHaveBeenCalledWith("token-123", {
      name: "Gerüst",
      description: null,
      structure: {
        name: "Abteilung",
        object_type_id: 1,
        children: [{ name: "Lose", object_type_id: null, children: [] }],
      },
    });

    fireEvent.change(within(child).getByLabelText("Ordnerklasse"), { target: { value: "2" } });
    fireEvent.click(within(form).getByRole("button", { name: "Speichern" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Vorlage „Gerüst“ gespeichert.");
  });

  it("captures a template from a folder chosen in the picker", async () => {
    api.listFolderTemplates.mockResolvedValue([]);
    api.listChildFolders.mockImplementation(async (_t, id) =>
      id === "root"
        ? [{ id: "f1", name: "Bauamt", parent_id: "root", object_type_id: 1 }]
        : [{ id: "f2", name: "Hochbau", parent_id: "f1", object_type_id: 2 }]
    );
    api.createFolderTemplate.mockResolvedValue({ id: "t9", name: "Struktur aus Hochbau" });
    api.getFolderTemplate.mockResolvedValue({
      id: "t9",
      name: "Struktur aus Hochbau",
      description: null,
      created_by: "x",
      created_at: "",
      structure: { name: "Hochbau", object_type_id: 2, children: [] },
    });
    wrap(<TemplateManager />);
    fireEvent.click(await screen.findByRole("button", { name: "Aus bestehendem Ordner übernehmen" }));
    fireEvent.click(await screen.findByRole("button", { name: "📁 Bauamt" }));
    fireEvent.click(await screen.findByRole("button", { name: "Struktur von „Hochbau“ übernehmen" }));
    await waitFor(() =>
      expect(api.createFolderTemplate).toHaveBeenCalledWith("token-123", {
        name: "Struktur aus Hochbau",
        sourceFolderId: "f2",
      })
    );
    expect(await screen.findByRole("form", { name: "Vorlage bearbeiten" })).toBeInTheDocument();
  });
});

describe("ObjectTypeEditor structure fields", () => {
  it("edits a folder class's lists, default and template-only flag", async () => {
    api.updateObjectType.mockResolvedValue({});
    wrap(<ObjectTypeEditor />);
    const row = (await screen.findByRole("cell", { name: "Abteilung" })).closest("tr")!;
    fireEvent.click(within(row).getByText("Bearbeiten"));

    const childFolders = await screen.findByRole("group", { name: "Unterordner" });
    expect(within(childFolders).getByLabelText("Team")).toBeChecked();
    const documents = screen.getByRole("group", { name: "Dokumente" });
    fireEvent.click(within(documents).getByLabelText("Nur ausgewählte"));
    fireEvent.click(within(documents).getByLabelText("Akte"));
    fireEvent.change(screen.getByLabelText("Vorgabe für Dokumente"), { target: { value: "Akte" } });
    fireEvent.click(screen.getByLabelText("Unterordner nur aus Strukturvorlagen"));

    fireEvent.submit(screen.getByRole("form", { name: "Objekttyp speichern" }));
    await waitFor(() =>
      expect(api.updateObjectType).toHaveBeenCalledWith(
        "token-123",
        1,
        expect.objectContaining({
          structure: {
            allowedChildFolderTypes: ["Team"],
            allowedDocumentTypes: ["Akte"],
            defaultChildFolderType: null,
            defaultDocumentType: "Akte",
            childrenFromTemplateOnly: true,
          },
        })
      )
    );
  });
});
