import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VorlagenPane } from "@/components/VorlagenPane";
import { I18nProvider } from "@/i18n";

const listFolderTemplatesMock = vi.fn();
const createFolderTemplateMock = vi.fn();
const deleteFolderTemplateMock = vi.fn();
const applyFolderTemplateMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listFolderTemplates: (...args: unknown[]) => listFolderTemplatesMock(...args),
    createFolderTemplate: (...args: unknown[]) => createFolderTemplateMock(...args),
    deleteFolderTemplate: (...args: unknown[]) => deleteFolderTemplateMock(...args),
    applyFolderTemplate: (...args: unknown[]) => applyFolderTemplateMock(...args),
  };
});

function renderPane() {
  return render(
    <I18nProvider>
      <VorlagenPane token="token-123" createdBy="alice" />
    </I18nProvider>
  );
}

const template1 = {
  id: "template-1",
  name: "Aktenplan-Rohbau",
  description: "Standard-Struktur für Bauprojekte",
  created_by: "alice",
  created_at: new Date().toISOString(),
};

describe("VorlagenPane", () => {
  beforeEach(() => {
    listFolderTemplatesMock.mockReset();
    createFolderTemplateMock.mockReset();
    deleteFolderTemplateMock.mockReset();
    applyFolderTemplateMock.mockReset();
    listFolderTemplatesMock.mockResolvedValue([]);
  });

  it("shows the empty state when there are no templates", async () => {
    renderPane();
    expect(await screen.findByText("Noch keine Vorlagen.")).toBeInTheDocument();
  });

  it("lists a template with its description", async () => {
    listFolderTemplatesMock.mockResolvedValue([template1]);

    renderPane();

    expect(await screen.findByText(/Aktenplan-Rohbau/)).toBeInTheDocument();
    expect(screen.getByText(/Standard-Struktur für Bauprojekte/)).toBeInTheDocument();
  });

  it("creates a template from a source folder id", async () => {
    listFolderTemplatesMock.mockResolvedValueOnce([]).mockResolvedValueOnce([template1]);
    createFolderTemplateMock.mockResolvedValue(template1);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Noch keine Vorlagen.");
    await user.type(screen.getByLabelText("Quellordner-ID"), "folder-abc");
    await user.type(screen.getByLabelText("Name"), "Aktenplan-Rohbau");
    await user.click(screen.getByText("Vorlage erfassen"));

    await waitFor(() =>
      expect(createFolderTemplateMock).toHaveBeenCalledWith(
        "token-123",
        expect.objectContaining({
          sourceFolderId: "folder-abc",
          name: "Aktenplan-Rohbau",
          createdBy: "alice",
        })
      )
    );
  });

  it("shows an error when creation fails", async () => {
    listFolderTemplatesMock.mockResolvedValue([]);
    createFolderTemplateMock.mockRejectedValue(new Error("boom"));

    const user = userEvent.setup();
    renderPane();

    await screen.findByText("Noch keine Vorlagen.");
    await user.type(screen.getByLabelText("Quellordner-ID"), "folder-abc");
    await user.type(screen.getByLabelText("Name"), "Vorlage");
    await user.click(screen.getByText("Vorlage erfassen"));

    expect(await screen.findByText("Erfassen fehlgeschlagen")).toBeInTheDocument();
  });

  it("applies a template to a target folder", async () => {
    listFolderTemplatesMock.mockResolvedValue([template1]);
    applyFolderTemplateMock.mockResolvedValue({
      root_folder: { id: "new-folder" },
      created_count: 3,
    });

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Aktenplan-Rohbau/);
    await user.click(screen.getByText("Anwenden"));
    await user.type(screen.getByLabelText("Zielordner-ID"), "target-folder");
    await user.click(screen.getByText("Anwendung bestätigen"));

    await waitFor(() =>
      expect(applyFolderTemplateMock).toHaveBeenCalledWith(
        "token-123",
        "template-1",
        expect.objectContaining({ targetParentId: "target-folder", createdBy: "alice" })
      )
    );
  });

  it("deletes a template after confirmation", async () => {
    listFolderTemplatesMock.mockResolvedValueOnce([template1]).mockResolvedValueOnce([]);
    deleteFolderTemplateMock.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Aktenplan-Rohbau/);
    await user.click(screen.getByText("Löschen"));

    await waitFor(() => expect(deleteFolderTemplateMock).toHaveBeenCalledWith("token-123", "template-1"));
  });

  it("does not delete when the confirmation dialog is dismissed", async () => {
    listFolderTemplatesMock.mockResolvedValue([template1]);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Aktenplan-Rohbau/);
    await user.click(screen.getByText("Löschen"));

    expect(deleteFolderTemplateMock).not.toHaveBeenCalled();
  });
});
