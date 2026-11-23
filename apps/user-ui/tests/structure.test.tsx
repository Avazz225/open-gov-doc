import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { TemplateApplyModal } from "@/components/TemplateApplyModal";
import { UploadForm } from "@/components/UploadForm";
import { I18nProvider } from "@/i18n";

// Filing structure in the user UI (P91-S3, ADR 0255): forms offer what the
// folder admits; the server checks either way.

const api = vi.hoisted(() => ({
  listObjectTypes: vi.fn(),
  getObjectTypeLayout: vi.fn(),
  uploadDocument: vi.fn(),
  getFolder: vi.fn(),
  getAllowedChildren: vi.fn(),
  listFolderTemplates: vi.fn(),
  getFolderTemplate: vi.fn(),
  applyFolderTemplate: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, ...api };
});

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return { ...actual, useAuth: () => ({ accessToken: "token-123", permissions: [] }) };
});

const DOCUMENT_TYPES = [
  { id: 11, name: "Akte", applies_to: "document", attributes: [] },
  { id: 12, name: "Brief", applies_to: "document", attributes: [] },
];
const FOLDER_TYPES = [
  { id: 1, name: "Vorgang", applies_to: "folder", attributes: [] },
  { id: 2, name: "Abteilung", applies_to: "folder", attributes: [] },
];

function allowed(overrides: Record<string, unknown> = {}) {
  return {
    enforcement: "enforce",
    folder_types: [],
    document_types: [],
    untyped_folders: true,
    untyped_documents: true,
    children_from_template_only: false,
    default_child_folder_type: null,
    default_document_type: null,
    ...overrides,
  };
}

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  api.getFolder.mockResolvedValue({ id: "f1", name: "Vorgang 7", object_type_id: 1 });
  api.getObjectTypeLayout.mockResolvedValue({ rows: [] });
});

describe("UploadForm", () => {
  it("offers only the folder's document types and preselects its default", async () => {
    api.listObjectTypes.mockResolvedValue(DOCUMENT_TYPES);
    api.getAllowedChildren.mockResolvedValue(
      allowed({ document_types: [{ id: 11, name: "Akte" }], untyped_documents: false, default_document_type: "Akte" })
    );
    render(
      <I18nProvider>
        <UploadForm token="token-123" folderId="f1" onUploaded={() => {}} onClose={() => {}} />
      </I18nProvider>
    );
    const select = await screen.findByLabelText("Objekttyp");
    await waitFor(() => expect(select).toHaveValue("11"));
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Akte"]);
    expect(api.getAllowedChildren).toHaveBeenCalledWith("token-123", { objectTypeId: 1 });
  });

  it("says so when the folder admits no documents", async () => {
    api.listObjectTypes.mockResolvedValue(DOCUMENT_TYPES);
    api.getAllowedChildren.mockResolvedValue(allowed({ untyped_documents: false }));
    render(
      <I18nProvider>
        <UploadForm token="token-123" folderId="root" onUploaded={() => {}} onClose={() => {}} />
      </I18nProvider>
    );
    expect(await screen.findByRole("note")).toHaveTextContent("keine Dokumente abgelegt");
    expect(api.getAllowedChildren).toHaveBeenCalledWith("token-123", { root: true });
    expect(screen.getByRole("button", { name: "Hochladen" })).toBeDisabled();
  });
});

describe("TemplateApplyModal", () => {
  it("offers only templates whose top folder fits and applies one", async () => {
    api.listObjectTypes.mockResolvedValue(FOLDER_TYPES);
    api.getAllowedChildren.mockResolvedValue(
      allowed({ folder_types: [{ id: 1, name: "Vorgang" }], untyped_folders: false })
    );
    api.listFolderTemplates.mockResolvedValue([{ id: "t1" }, { id: "t2" }, { id: "t3" }]);
    api.getFolderTemplate.mockImplementation(async (_t: string, id: string) => ({
      id,
      name: { t1: "Vorgangsakte", t2: "Abteilungsgerüst", t3: "Lose Ordner" }[id],
      description: null,
      structure: { name: "x", object_type_id: { t1: 1, t2: 2, t3: null }[id], children: [] },
    }));
    api.applyFolderTemplate.mockResolvedValue({ root_folder: {}, created_count: 3 });
    const onApplied = vi.fn();
    render(
      <I18nProvider>
        <TemplateApplyModal target={{ id: "f1", name: "Team Hochbau" }} onApplied={onApplied} onClose={() => {}} />
      </I18nProvider>
    );
    const dialog = await screen.findByRole("dialog", { name: "Struktur aus Vorlage in „Team Hochbau“" });
    const apply = await within(dialog).findByRole("button", { name: "Vorlage „Vorgangsakte“ anlegen" });
    expect(within(dialog).queryByText("Abteilungsgerüst")).not.toBeInTheDocument();
    expect(within(dialog).queryByText("Lose Ordner")).not.toBeInTheDocument();
    fireEvent.click(apply);
    await waitFor(() => expect(onApplied).toHaveBeenCalledWith(3));
    expect(api.applyFolderTemplate).toHaveBeenCalledWith("token-123", "t1", { targetParentId: "f1" });
  });
});
